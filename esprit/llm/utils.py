import html
import re
from typing import Any


def _truncate_to_first_function(content: str) -> str:
    if not content:
        return content

    function_starts = [match.start() for match in re.finditer(r"<function=", content)]

    if len(function_starts) >= 2:
        second_function_start = function_starts[1]

        return content[:second_function_start].rstrip()

    return content


def parse_tool_invocations(content: str) -> list[dict[str, Any]] | None:
    content = fix_incomplete_tool_call(content)

    tool_invocations: list[dict[str, Any]] = []

    fn_regex_pattern = r"<function=([^>]+)>\n?(.*?)</function>"
    fn_param_regex_pattern = r"<parameter=([^>]+)>(.*?)</parameter>"

    fn_matches = re.finditer(fn_regex_pattern, content, re.DOTALL)

    for fn_match in fn_matches:
        fn_name = fn_match.group(1)
        fn_body = fn_match.group(2)

        param_matches = re.finditer(fn_param_regex_pattern, fn_body, re.DOTALL)

        args = {}
        for param_match in param_matches:
            param_name = param_match.group(1)
            param_value = param_match.group(2).strip()

            param_value = html.unescape(param_value)
            args[param_name] = param_value

        tool_invocations.append({"toolName": fn_name, "args": args})

    return tool_invocations if tool_invocations else None


def fix_incomplete_tool_call(content: str) -> str:
    """Fix incomplete tool calls by adding missing </function> tag."""
    if (
        "<function=" in content
        and content.count("<function=") == 1
        and "</function>" not in content
    ):
        content = content.rstrip()
        content = content + "function>" if content.endswith("</") else content + "\n</function>"
    return content


def format_tool_call(tool_name: str, args: dict[str, Any]) -> str:
    xml_parts = [f"<function={tool_name}>"]

    for key, value in args.items():
        xml_parts.append(f"<parameter={key}>{value}</parameter>")

    xml_parts.append("</function>")

    return "\n".join(xml_parts)


def clean_content(content: str) -> str:
    if not content:
        return ""

    content = fix_incomplete_tool_call(content)

    tool_pattern = r"<function=[^>]+>.*?</function>"
    cleaned = re.sub(tool_pattern, "", content, flags=re.DOTALL)

    incomplete_tool_pattern = r"<function=[^>]+>.*$"
    cleaned = re.sub(incomplete_tool_pattern, "", cleaned, flags=re.DOTALL)

    partial_tag_pattern = r"<f(?:u(?:n(?:c(?:t(?:i(?:o(?:n(?:=(?:[^>]*)?)?)?)?)?)?)?)?)?$"
    cleaned = re.sub(partial_tag_pattern, "", cleaned)

    hidden_xml_patterns = [
        r"<inter_agent_message>.*?</inter_agent_message>",
        r"<agent_completion_report>.*?</agent_completion_report>",
    ]
    for pattern in hidden_xml_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.DOTALL | re.IGNORECASE)

    cleaned = re.sub(r"\n\s*\n", "\n\n", cleaned)

    return cleaned.strip()


def _extract_declared_tool_call_ids(message: dict[str, Any]) -> set[str]:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return set()

    ids: set[str] = set()
    for tool_call in tool_calls:
        if not isinstance(tool_call, dict):
            continue
        tool_call_id = str(tool_call.get("id") or "")
        if tool_call_id:
            ids.add(tool_call_id)
    return ids


def _strip_tool_calls_metadata(message: dict[str, Any]) -> dict[str, Any]:
    stripped = dict(message)
    stripped.pop("tool_calls", None)
    return stripped


def _convert_tool_to_user_fallback(message: dict[str, Any]) -> dict[str, Any]:
    converted = {
        k: v for k, v in message.items() if k not in {"role", "tool_call_id", "tool_calls"}
    }
    converted["role"] = "user"

    prefix = (
        "Tool result replayed as context because tool metadata was incomplete.\n"
    )
    content = message.get("content", "")
    if isinstance(content, str):
        converted["content"] = f"{prefix}{content}"
        return converted

    if isinstance(content, list):
        converted["content"] = [{"type": "text", "text": prefix}, *content]
        return converted

    converted["content"] = f"{prefix}{content}"
    return converted


def normalize_messages_for_provider(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize message history to avoid invalid provider payloads.

    Some providers (notably Anthropic) require strict assistant/tool adjacency:
    - assistant `tool_calls` must be followed immediately by matching `role=tool` results
    - each tool result must reference a declared tool_call_id

    If the sequence is malformed (missing IDs, missing results, or non-tool messages between),
    we downgrade tool metadata to plain user/assistant context to keep payloads valid.
    """
    normalized: list[dict[str, Any]] = []
    i = 0
    total = len(messages)

    while i < total:
        message = messages[i]
        role = message.get("role")

        if role == "assistant" and isinstance(message.get("tool_calls"), list):
            declared_ids = _extract_declared_tool_call_ids(message)
            if not declared_ids:
                normalized.append(_strip_tool_calls_metadata(message))
                i += 1
                continue

            j = i + 1
            immediate_tools: list[dict[str, Any]] = []
            while j < total and messages[j].get("role") == "tool":
                immediate_tools.append(messages[j])
                j += 1

            immediate_ids = {
                str(tool_msg.get("tool_call_id") or "")
                for tool_msg in immediate_tools
                if str(tool_msg.get("tool_call_id") or "")
            }
            all_immediate_have_id = all(
                bool(str(tool_msg.get("tool_call_id") or "")) for tool_msg in immediate_tools
            )
            only_declared_ids = all(
                str(tool_msg.get("tool_call_id") or "") in declared_ids for tool_msg in immediate_tools
            )
            has_all_declared = declared_ids.issubset(immediate_ids)

            if immediate_tools and all_immediate_have_id and only_declared_ids and has_all_declared:
                normalized.append(message)
                normalized.extend(immediate_tools)
                i = j
                continue

            normalized.append(_strip_tool_calls_metadata(message))
            i += 1
            continue

        if role != "tool":
            normalized.append(message)
            i += 1
            continue

        normalized.append(_convert_tool_to_user_fallback(message))
        i += 1

    return normalized
