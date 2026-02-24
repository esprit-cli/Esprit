"""
Format conversion between OpenAI-style messages and Google Cloud Code API.

Handles:
- Message format conversion (system, user, assistant, tool)
- Tool schema sanitization (JSON Schema → Google format)
- SSE response stream parsing
- Cloud Code request envelope wrapping
"""

import hashlib
import json
import logging
import platform
import uuid
from typing import Any

logger = logging.getLogger(__name__)

# Type mapping for JSON Schema → Google GenAI format
_TYPE_MAP = {
    "string": "STRING",
    "number": "NUMBER",
    "integer": "INTEGER",
    "boolean": "BOOLEAN",
    "array": "ARRAY",
    "object": "OBJECT",
}

# JSON Schema keywords not supported by Google GenAI
_UNSUPPORTED_KEYWORDS = {
    "additionalProperties",
    "default",
    "$schema",
    "$defs",
    "definitions",
    "$ref",
    "$id",
    "$comment",
    "title",
    "minLength",
    "maxLength",
    "pattern",
    "format",
    "minItems",
    "maxItems",
    "examples",
    "allOf",
    "anyOf",
    "oneOf",
}


# ── Schema Sanitization ─────────────────────────────────────────


def _sanitize_schema(schema: Any) -> dict[str, Any] | None:
    """Convert JSON Schema to Google GenAI-compatible format."""
    if not isinstance(schema, dict):
        return None

    result: dict[str, Any] = {}

    # Handle type
    raw_type = schema.get("type")
    if isinstance(raw_type, list):
        # ["string", "null"] → "string"
        non_null = [t for t in raw_type if t != "null"]
        raw_type = non_null[0] if non_null else "string"

    if raw_type and raw_type in _TYPE_MAP:
        result["type"] = _TYPE_MAP[raw_type]

    # Handle anyOf/oneOf — pick first non-null
    for key in ("anyOf", "oneOf"):
        variants = schema.get(key)
        if variants and isinstance(variants, list):
            for v in variants:
                if isinstance(v, dict) and v.get("type") != "null":
                    merged = _sanitize_schema(v)
                    if merged:
                        result.update(merged)
                    break
            if "type" not in result:
                result["type"] = "STRING"

    # Description
    if desc := schema.get("description"):
        result["description"] = str(desc)

    # Enum
    if enum := schema.get("enum"):
        result["enum"] = [str(e) for e in enum]

    # Properties (for objects)
    if props := schema.get("properties"):
        sanitized_props = {}
        for name, prop_schema in props.items():
            sanitized = _sanitize_schema(prop_schema)
            if sanitized:
                sanitized_props[name] = sanitized
        if sanitized_props:
            result["properties"] = sanitized_props

    # Required
    if req := schema.get("required"):
        if isinstance(req, list):
            # Only keep required fields that exist in properties
            if "properties" in result:
                result["required"] = [
                    r for r in req if r in result["properties"]
                ]
            else:
                result["required"] = list(req)

    # Items (for arrays)
    if items := schema.get("items"):
        sanitized = _sanitize_schema(items)
        if sanitized:
            result["items"] = sanitized

    # Default type if nothing was set
    if "type" not in result:
        if "properties" in result:
            result["type"] = "OBJECT"
        else:
            result["type"] = "STRING"

    return result


# ── Message Conversion ───────────────────────────────────────────


def _convert_content_part(part: dict[str, Any]) -> dict[str, Any]:
    """Convert a single OpenAI content part to Google GenAI part."""
    part_type = part.get("type", "text")

    if part_type == "text":
        return {"text": part.get("text", "")}

    if part_type == "image_url":
        url_data = part.get("image_url", {})
        url = url_data.get("url", "")
        if url.startswith("data:"):
            # data:image/png;base64,xxxxx
            header, data = url.split(",", 1)
            mime = header.split(";")[0].split(":")[1]
            return {"inlineData": {"mimeType": mime, "data": data}}
        return {"fileData": {"mimeType": "image/jpeg", "fileUri": url}}

    # Fallback: treat as text
    return {"text": str(part.get("text", part.get("content", "")))}


def _convert_tool_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Convert OpenAI tool_call to Google functionCall."""
    func = tool_call.get("function", {})
    args = func.get("arguments", "{}")
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            args = {"raw": args}
    return {
        "functionCall": {
            "name": func.get("name", ""),
            "args": args,
            "id": tool_call.get("id", ""),
        }
    }


def _normalize_tool_response_content(content: Any) -> dict[str, Any]:
    """Normalize role=tool content to an object payload for functionResponse."""
    if isinstance(content, dict):
        return content

    if isinstance(content, str):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {"result": content}

    if isinstance(content, list):
        text_parts: list[str] = []
        images: list[dict[str, Any]] = []
        other_parts: list[Any] = []

        for part in content:
            if not isinstance(part, dict):
                other_parts.append(part)
                continue

            part_type = part.get("type")
            if part_type == "text":
                text_parts.append(str(part.get("text", "")))
                continue

            if part_type == "image_url":
                image_url = part.get("image_url")
                if isinstance(image_url, dict):
                    images.append(image_url)
                elif isinstance(image_url, str):
                    images.append({"url": image_url})
                else:
                    other_parts.append(part)
                continue

            other_parts.append(part)

        normalized: dict[str, Any] = {}
        result_text = "\n".join(text for text in text_parts if text)
        if result_text:
            normalized["result"] = result_text
        if images:
            normalized["images"] = images
        if other_parts:
            normalized["parts"] = other_parts

        if normalized:
            return normalized

    return {"result": str(content)}


def _convert_messages(messages: list[dict[str, Any]]) -> tuple[
    dict[str, Any] | None,  # systemInstruction
    list[dict[str, Any]],  # contents
]:
    """Convert OpenAI-style messages to Google GenAI format.

    Returns (systemInstruction, contents).
    """
    system_parts: list[dict[str, Any]] = []
    contents: list[dict[str, Any]] = []
    # Map tool_call_id → function_name for resolving functionResponse names
    tc_id_to_name: dict[str, str] = {}

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            if isinstance(content, str):
                system_parts.append({"text": content})
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        system_parts.append(_convert_content_part(part))
                    else:
                        system_parts.append({"text": str(part)})
            continue

        if role == "tool":
            # Tool result → functionResponse
            tool_call_id = msg.get("tool_call_id", "")
            # Resolve the function name from a prior assistant message's tool_calls
            func_name = tc_id_to_name.get(tool_call_id, tool_call_id)
            result_content = _normalize_tool_response_content(content)

            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": func_name,
                        "response": result_content,
                        "id": tool_call_id,
                    }
                }],
            })
            continue

        # user or assistant
        google_role = "model" if role == "assistant" else "user"
        parts: list[dict[str, Any]] = []

        if isinstance(content, str) and content:
            parts.append({"text": content})
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    parts.append(_convert_content_part(part))
                else:
                    parts.append({"text": str(part)})

        # Handle tool calls in assistant messages
        tool_calls = msg.get("tool_calls", [])
        for tc in tool_calls:
            # Record tool_call_id → function name for later functionResponse resolution
            tc_id = tc.get("id", "")
            tc_func_name = tc.get("function", {}).get("name", "")
            if tc_id and tc_func_name:
                tc_id_to_name[tc_id] = tc_func_name
            parts.append(_convert_tool_call(tc))

        if parts:
            contents.append({"role": google_role, "parts": parts})

    system_instruction = None
    if system_parts:
        system_instruction = {"role": "user", "parts": system_parts}

    return system_instruction, contents


def _convert_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    """Convert OpenAI-style tool definitions to Google format."""
    if not tools:
        return None

    declarations = []
    for tool in tools:
        if tool.get("type") != "function":
            continue
        func = tool.get("function", {})
        params = func.get("parameters")
        sanitized_params = _sanitize_schema(params) if params else None

        decl: dict[str, Any] = {
            "name": func.get("name", ""),
            "description": func.get("description", ""),
        }
        if sanitized_params:
            decl["parameters"] = sanitized_params
        declarations.append(decl)

    if not declarations:
        return None
    return [{"functionDeclarations": declarations}]


# ── Request Building ─────────────────────────────────────────────


def build_cloudcode_request(
    messages: list[dict[str, Any]],
    model: str,
    project_id: str,
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    top_p: float | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a complete Cloud Code API request envelope.

    Args:
        messages: OpenAI-style messages
        model: Model name (e.g., "claude-opus-4-6-thinking")
        project_id: Cloud Code project ID
        max_tokens: Maximum output tokens
        temperature: Sampling temperature
        top_p: Top-p sampling
        tools: OpenAI-style tool definitions

    Returns:
        Complete request body for Cloud Code API
    """
    system_instruction, contents = _convert_messages(messages)

    # Generation config
    gen_config: dict[str, Any] = {}
    if max_tokens:
        gen_config["maxOutputTokens"] = max_tokens
    if temperature is not None:
        gen_config["temperature"] = temperature
    if top_p is not None:
        gen_config["topP"] = top_p

    # Thinking config for thinking models
    is_thinking = "thinking" in model
    is_claude = "claude" in model
    if is_thinking:
        thinking_budget = 32768
        if is_claude:
            # Cloud Code API wraps Anthropic's native API — it uses snake_case
            # field names (include_thoughts, thinking_budget) rather than
            # Anthropic's direct format (type: "enabled", budget_tokens).
            gen_config["thinkingConfig"] = {
                "include_thoughts": True,
                "thinking_budget": thinking_budget,
            }
            current_max = gen_config.get("maxOutputTokens", 0)
            if current_max <= thinking_budget:
                gen_config["maxOutputTokens"] = thinking_budget + 16384
        else:
            # Gemini uses camelCase
            gen_config["thinkingConfig"] = {
                "includeThoughts": True,
                "thinkingBudget": 16384,
            }

    # Inner request
    request: dict[str, Any] = {"contents": contents}
    if system_instruction:
        request["systemInstruction"] = system_instruction
    if gen_config:
        request["generationConfig"] = gen_config

    # Tools
    google_tools = _convert_tools(tools)
    if google_tools:
        request["tools"] = google_tools
        # Claude needs VALIDATED mode for strict param checking
        if "claude" in model:
            request["toolConfig"] = {
                "functionCallingConfig": {"mode": "VALIDATED"}
            }

    # Session ID for prompt cache continuity
    first_user_text = ""
    for msg in messages:
        if msg.get("role") == "user":
            c = msg.get("content", "")
            if isinstance(c, str):
                first_user_text = c
            elif isinstance(c, list) and c:
                first_user_text = str(c[0].get("text", ""))
            break
    if first_user_text:
        request["sessionId"] = hashlib.sha256(
            first_user_text.encode()
        ).hexdigest()[:32]

    return {
        "project": project_id,
        "model": model,
        "request": request,
        "requestType": "agent",
        "userAgent": "antigravity",
        "requestId": f"agent-{uuid.uuid4().hex[:12]}",
    }


def build_request_headers(
    access_token: str,
    model: str,
) -> dict[str, str]:
    """Build HTTP headers for a Cloud Code API request."""
    system = platform.system().lower()
    arch = platform.machine()

    headers: dict[str, str] = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": f"antigravity/1.15.8 {system}/{arch}",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps(
            {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}
        ),
    }

    if "claude" in model and "thinking" in model:
        headers["anthropic-beta"] = "interleaved-thinking-2025-05-14"

    return headers


# ── Response Parsing ─────────────────────────────────────────────


def parse_sse_chunk(chunk_data: dict[str, Any]) -> tuple[
    str,  # text content
    list[dict[str, Any]],  # thinking blocks
    list[dict[str, Any]],  # tool calls
    dict[str, int],  # usage
]:
    """Parse a single SSE data chunk from Cloud Code API.

    Returns (text, thinking_blocks, tool_calls, usage).
    """
    text = ""
    thinking_blocks: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    usage: dict[str, int] = {}

    # Navigate to the response data
    response = chunk_data
    if "response" in chunk_data:
        response = chunk_data["response"]

    candidates = response.get("candidates", [])
    if not candidates:
        # Check for usage-only chunk
        if um := response.get("usageMetadata"):
            usage = _parse_usage(um)
        return text, thinking_blocks, tool_calls, usage

    candidate = candidates[0]
    content = candidate.get("content", {})
    parts = content.get("parts", [])

    for part in parts:
        if "functionCall" in part:
            fc = part["functionCall"]
            tool_calls.append({
                "id": fc.get("id", f"call_{uuid.uuid4().hex[:8]}"),
                "type": "function",
                "function": {
                    "name": fc.get("name", ""),
                    "arguments": json.dumps(fc.get("args", {})),
                },
            })
        elif part.get("thought"):
            thinking_blocks.append({
                "type": "thinking",
                "thinking": part.get("text", ""),
            })
        elif "text" in part:
            text += part["text"]

    if um := response.get("usageMetadata"):
        usage = _parse_usage(um)

    return text, thinking_blocks, tool_calls, usage


def parse_finish_reason(chunk_data: dict[str, Any]) -> str | None:
    """Extract finish reason from a Cloud Code response chunk."""
    response = chunk_data
    if "response" in chunk_data:
        response = chunk_data["response"]

    candidates = response.get("candidates", [])
    if not candidates:
        return None

    reason = candidates[0].get("finishReason")
    if reason == "STOP":
        return "end_turn"
    if reason == "MAX_TOKENS":
        return "max_tokens"
    if reason == "TOOL_USE":
        return "tool_use"
    return reason


def _parse_usage(um: dict[str, Any]) -> dict[str, int]:
    """Parse usageMetadata into token counts."""
    prompt = um.get("promptTokenCount", 0)
    cached = um.get("cachedContentTokenCount", 0)
    output = um.get("candidatesTokenCount", 0)
    return {
        "input_tokens": max(0, prompt - cached),
        "output_tokens": output,
        "cached_tokens": cached,
    }
