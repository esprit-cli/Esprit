import asyncio
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx
import litellm
from jinja2 import Environment, FileSystemLoader, select_autoescape
from litellm import acompletion, stream_chunk_builder, supports_reasoning
from litellm.utils import supports_prompt_caching, supports_vision

from esprit.config import Config
from esprit.llm.config import LLMConfig
from esprit.llm.memory_compressor import MemoryCompressor
from esprit.llm.utils import (
    _truncate_to_first_function,
    fix_incomplete_tool_call,
    parse_tool_invocations,
)
from esprit.skills import load_skills
from esprit.tools import get_tools_prompt
from esprit.utils.resource_paths import get_esprit_resource_path

# Provider OAuth integration (Codex, Copilot, Gemini, Anthropic, Antigravity)
try:
    from esprit.providers.litellm_integration import (
        get_provider_headers,
        should_use_oauth,
        get_provider_api_key,
        get_auth_client,
    )
    from esprit.providers.account_pool import get_account_pool
    from esprit.providers.antigravity import ANTIGRAVITY_MODELS, ENDPOINTS
    from esprit.providers.antigravity_format import (
        build_cloudcode_request,
        build_request_headers,
        parse_sse_chunk,
    )
    PROVIDERS_AVAILABLE = True
except ImportError:
    PROVIDERS_AVAILABLE = False

logger = logging.getLogger(__name__)


def _mask_email(email: str) -> str:
    """Mask email for logging to avoid PII exposure."""
    if "@" in email:
        local, domain = email.rsplit("@", 1)
        return f"{local[:3]}***@{domain[:3]}***"
    return email[:3] + "***"


litellm.drop_params = True
litellm.modify_params = True

# Register Codex models that may not yet be in litellm's model cost map.
# litellm needs mode=responses so it routes to /responses instead of /chat/completions.
_CODEX_BASE_INFO = {
    "mode": "responses",
    "max_input_tokens": 272000,
    "max_output_tokens": 128000,
    "supports_function_calling": True,
    "supports_vision": True,
    "supports_reasoning": True,
    "supports_native_streaming": True,
}
for _base in ["gpt-5.3-codex", "gpt-5.2-codex"]:
    if _base not in litellm.model_cost:
        litellm.model_cost[_base] = {**_CODEX_BASE_INFO, "litellm_provider": "openai"}


class LLMRequestFailedError(Exception):
    def __init__(self, message: str, details: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


@dataclass
class LLMResponse:
    content: str
    tool_invocations: list[dict[str, Any]] | None = None
    thinking_blocks: list[dict[str, Any]] | None = None


@dataclass
class RequestStats:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost: float = 0.0
    requests: int = 0
    last_input_tokens: int = 0  # most recent request's input tokens (= context window usage)

    def to_dict(self) -> dict[str, int | float]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cost": round(self.cost, 4),
            "requests": self.requests,
        }


class LLM:
    def __init__(self, config: LLMConfig, agent_name: str | None = None):
        self.config = config
        self.agent_name = agent_name
        self.agent_id: str | None = None
        self._total_stats = RequestStats()
        self.memory_compressor = MemoryCompressor(model_name=config.model_name)
        self.system_prompt = self._load_system_prompt(agent_name)

        reasoning = Config.get("esprit_reasoning_effort")
        if reasoning:
            self._reasoning_effort = reasoning
        elif config.scan_mode == "quick":
            self._reasoning_effort = "medium"
        else:
            self._reasoning_effort = "high"

    def _load_system_prompt(self, agent_name: str | None) -> str:
        if not agent_name:
            return ""

        try:
            prompt_dir = get_esprit_resource_path("agents", agent_name)
            skills_dir = get_esprit_resource_path("skills")
            env = Environment(
                loader=FileSystemLoader([prompt_dir, skills_dir]),
                autoescape=select_autoescape(enabled_extensions=(), default_for_string=False),
            )

            skills_to_load = [
                *list(self.config.skills or []),
                f"scan_modes/{self.config.scan_mode}",
            ]
            skill_content = load_skills(skills_to_load)
            env.globals["get_skill"] = lambda name: skill_content.get(name, "")

            result = env.get_template("system_prompt.jinja").render(
                get_tools_prompt=get_tools_prompt,
                native_tools_enabled=self.supports_native_tool_calling(),
                loaded_skill_names=list(skill_content.keys()),
                **skill_content,
            )
            return str(result)
        except Exception:  # noqa: BLE001
            return ""

    def set_agent_identity(self, agent_name: str | None, agent_id: str | None) -> None:
        if agent_name:
            self.agent_name = agent_name
        if agent_id:
            self.agent_id = agent_id

    def supports_native_tool_calling(self) -> bool:
        if not self.config.model_name:
            return False

        if self._is_antigravity():
            return True

        try:
            return bool(litellm.supports_function_calling(model=self.config.model_name))
        except Exception:  # noqa: BLE001
            return False

    async def generate(
        self, conversation_history: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None
    ) -> AsyncIterator[LLMResponse]:
        messages = self._prepare_messages(conversation_history)
        max_retries = int(Config.get("esprit_llm_max_retries") or "5")

        attempt = 0
        while attempt <= max_retries:
            try:
                if self._is_antigravity():
                    async for response in self._stream_antigravity(messages, tools=tools):
                        yield response
                else:
                    async for response in self._stream(messages, tools=tools):
                        yield response
                return  # noqa: TRY300
            except Exception as e:  # noqa: BLE001
                # Try account rotation on rate limit (429)
                if self._try_rotate_on_rate_limit(e):
                    # Rotated to a new account — retry immediately (don't increment)
                    continue
                if attempt >= max_retries or not self._should_retry(e):
                    # Before giving up, try auto model fallback for Antigravity
                    if self._is_antigravity() and self._try_model_fallback(e):
                        # Switched to fallback model — restart retry loop
                        attempt = 0
                        continue
                    self._raise_error(e)
                wait = min(10, 2 * (2**attempt))
                await asyncio.sleep(wait)
                attempt += 1

    async def _stream(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> AsyncIterator[LLMResponse]:
        accumulated = ""
        chunks: list[Any] = []
        done_streaming = 0

        self._total_stats.requests += 1
        response = await acompletion(**self._build_completion_args(messages, tools=tools), stream=True)

        async for chunk in response:
            chunks.append(chunk)
            if done_streaming:
                done_streaming += 1
                if getattr(chunk, "usage", None) or done_streaming > 5:
                    break
                continue
            delta = self._get_chunk_content(chunk)
            if delta:
                accumulated += delta
                if "</function>" in accumulated:
                    accumulated = accumulated[
                        : accumulated.find("</function>") + len("</function>")
                    ]
                    yield LLMResponse(content=accumulated)
                    done_streaming = 1
                    continue
                yield LLMResponse(content=accumulated)

        assembled = stream_chunk_builder(chunks) if chunks else None
        if assembled:
            self._update_usage_stats(assembled)

        # Try native tool calls first, fall back to XML parsing
        native_tool_calls = self._extract_native_tool_calls(assembled)
        if native_tool_calls:
            yield LLMResponse(
                content=accumulated,
                tool_invocations=native_tool_calls,
                thinking_blocks=self._extract_thinking(chunks),
            )
        else:
            accumulated = fix_incomplete_tool_call(_truncate_to_first_function(accumulated))
            yield LLMResponse(
                content=accumulated,
                tool_invocations=parse_tool_invocations(accumulated),
                thinking_blocks=self._extract_thinking(chunks),
            )

    async def _stream_antigravity(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> AsyncIterator[LLMResponse]:
        """Stream responses from the Antigravity Cloud Code API directly."""
        self._total_stats.requests += 1

        # Get credentials and project info
        client = get_auth_client()
        credentials = client.get_credentials("antigravity")
        if not credentials or credentials.type != "oauth":
            raise LLMRequestFailedError("No Antigravity credentials. Run 'esprit provider login' first.")

        credentials = await client.ensure_valid_credentials("antigravity", credentials)
        access_token = credentials.access_token
        project_id = credentials.extra.get("project_id")

        # Handle project_id stored as dict (older credential format)
        if isinstance(project_id, dict):
            project_id = project_id.get("id")

        # Re-discover project if missing
        if not project_id:
            from esprit.providers.antigravity import _discover_project
            project_id, _ = await _discover_project(access_token)
            if not project_id:
                raise LLMRequestFailedError("No Antigravity project ID. Re-login with 'esprit provider login'.")

        # Extract bare model name (strip any provider prefix like "antigravity/", "google/")
        model = self.config.model_name
        if "/" in model:
            model = model.split("/", 1)[1]

        # Build Cloud Code request
        request_body = build_cloudcode_request(
            messages=messages,
            model=model,
            project_id=project_id,
            max_tokens=int(Config.get("esprit_max_tokens") or "16384"),
            tools=tools,
        )
        headers = build_request_headers(access_token, model)

        # Try endpoints in order; skip production for Claude models
        is_claude = "claude" in model
        last_error = None
        for endpoint in ENDPOINTS:
            # Production endpoint doesn't support Claude — skip it
            if is_claude and "sandbox" not in endpoint:
                continue
            url = f"{endpoint}/v1internal:streamGenerateContent?alt=sse"
            try:
                async for response in self._do_antigravity_stream(url, headers, request_body):
                    yield response
                return
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Mark rate-limited and let generate() handle rotation
                    raise
                if e.response.status_code in (401, 403):
                    # Auth errors — retrying won't help
                    raise
                if e.response.status_code == 400:
                    # Cloud Code 400s can be transient — retry on same endpoint
                    retried = False
                    last_retry_error = e
                    for retry in range(2):
                        await asyncio.sleep(2 * (retry + 1))
                        try:
                            async for response in self._do_antigravity_stream(url, headers, request_body):
                                yield response
                            retried = True
                            return
                        except httpx.HTTPStatusError as retry_e:
                            last_retry_error = retry_e
                            if retry_e.response.status_code != 400:
                                raise
                    if not retried:
                        raise last_retry_error
                if e.response.status_code == 404:
                    # Model not found on this endpoint, try next
                    last_error = e
                    continue
                last_error = e
            except httpx.ConnectError:
                # Don't overwrite a more informative error from a previous endpoint
                continue

        if last_error:
            raise last_error
        raise LLMRequestFailedError("All Antigravity endpoints unreachable.")

    async def _do_antigravity_stream(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> AsyncIterator[LLMResponse]:
        """Execute a single SSE stream against the Cloud Code API."""
        accumulated = ""
        all_thinking: list[dict[str, Any]] = []
        all_tool_calls: list[dict[str, Any]] = []
        total_usage: dict[str, int] = {}
        done_streaming = False

        async with httpx.AsyncClient(timeout=120) as http:
            async with http.stream("POST", url, headers=headers, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if done_streaming:
                        break

                    if line.startswith("data: "):
                        data_str = line[6:]
                        if data_str.strip() == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        text, thinking, tool_calls, usage = parse_sse_chunk(chunk)
                        if thinking:
                            all_thinking.extend(thinking)
                        if tool_calls:
                            all_tool_calls.extend(tool_calls)
                        if usage:
                            total_usage = usage

                        if text:
                            accumulated += text
                            if "</function>" in accumulated:
                                accumulated = accumulated[
                                    : accumulated.find("</function>") + len("</function>")
                                ]
                                yield LLMResponse(content=accumulated)
                                done_streaming = True
                                continue
                            yield LLMResponse(content=accumulated)

        # Update usage stats
        if total_usage:
            req_input = total_usage.get("input_tokens", 0)
            req_output = total_usage.get("output_tokens", 0)
            req_cached = total_usage.get("cached_tokens", 0)
            self._total_stats.input_tokens += req_input
            self._total_stats.output_tokens += req_output
            self._total_stats.cached_tokens += req_cached
            self._total_stats.last_input_tokens = req_input

            # Calculate cost via pricing DB
            from esprit.llm.pricing import get_pricing_db

            self._total_stats.cost += get_pricing_db().get_cost(
                self.config.model_name or "", req_input, req_output, req_cached,
            )

        accumulated = fix_incomplete_tool_call(_truncate_to_first_function(accumulated))
        # Convert native tool calls from Cloud Code if present
        native_invocations = None
        if all_tool_calls:
            converted_invocations = []
            for tc in all_tool_calls:
                function_payload = tc.get("function", {})
                if not isinstance(function_payload, dict):
                    function_payload = {}

                tool_name = tc.get("name") or function_payload.get("name", "")
                if not tool_name:
                    continue

                raw_args = tc.get("args")
                if raw_args is None:
                    raw_args = tc.get("arguments")
                if raw_args is None:
                    raw_args = function_payload.get("arguments", function_payload.get("args", {}))

                tc_args: dict[str, Any]
                if isinstance(raw_args, str):
                    try:
                        parsed = json.loads(raw_args)
                        tc_args = parsed if isinstance(parsed, dict) else {}
                    except (json.JSONDecodeError, ValueError):
                        tc_args = {}
                elif isinstance(raw_args, dict):
                    tc_args = raw_args
                else:
                    tc_args = {}

                tool_call_id = tc.get("id") or tc.get("tool_call_id", "")

                converted_invocations.append({
                    "toolName": tool_name,
                    "args": tc_args,
                    "tool_call_id": tool_call_id,
                })
            native_invocations = converted_invocations or None
        yield LLMResponse(
            content=accumulated,
            tool_invocations=native_invocations or parse_tool_invocations(accumulated),
            thinking_blocks=all_thinking or None,
        )

    def _prepare_messages(self, conversation_history: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages = [{"role": "system", "content": self.system_prompt}]

        if self.agent_name:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"\n\n<agent_identity>\n"
                        f"<meta>Internal metadata: do not echo or reference.</meta>\n"
                        f"<agent_name>{self.agent_name}</agent_name>\n"
                        f"<agent_id>{self.agent_id}</agent_id>\n"
                        f"</agent_identity>\n\n"
                    ),
                }
            )

        compressed = list(self._compress_with_tracer_signal(conversation_history))
        conversation_history.clear()
        conversation_history.extend(compressed)
        messages.extend(compressed)

        if self._is_anthropic() and self.config.enable_prompt_caching:
            messages = self._add_cache_control(messages)

        return messages

    def _compress_with_tracer_signal(
        self, conversation_history: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Run memory compression, signalling the tracer so the TUI can show progress."""
        from esprit.telemetry.tracer import get_global_tracer

        tracer = get_global_tracer()
        agent_id = self.agent_id
        if tracer and agent_id:
            tracer.compacting_agents.add(agent_id)
        try:
            return self.memory_compressor.compress_history(conversation_history)
        finally:
            if tracer and agent_id:
                tracer.compacting_agents.discard(agent_id)

    def _build_completion_args(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        if not self._supports_vision():
            messages = self._strip_images(messages)

        args: dict[str, Any] = {
            "model": self.config.model_name,
            "messages": messages,
            "timeout": self.config.timeout,
            "stream_options": {"include_usage": True},
        }

        if tools:
            args["tools"] = tools

        # Translate google/ → gemini/ for litellm compatibility
        if self.config.model_name and self.config.model_name.lower().startswith("google/"):
            args["model"] = "gemini/" + self.config.model_name.split("/", 1)[1]

        # Check for provider OAuth authentication first (Codex, Copilot, Gemini, etc.)
        use_oauth = False
        if PROVIDERS_AVAILABLE and self.config.model_name:
            use_oauth = should_use_oauth(self.config.model_name)
            if use_oauth:
                model_lower = self.config.model_name.lower()

                # Codex models use OpenAI's Responses API (mode=responses
                # in model_cost).  Route through the standard openai provider
                # with Esprit's OAuth token — avoids litellm's chatgpt/
                # provider which has auth-file bugs with external tokens.
                if "codex" in model_lower:
                    bare_model = self.config.model_name.split("/", 1)[-1]
                    args["model"] = bare_model
                    args["api_key"] = get_provider_api_key(self.config.model_name) or "oauth-auth"
                else:
                    provider_headers = get_provider_headers(self.config.model_name)
                    if provider_headers:
                        args["extra_headers"] = provider_headers
                    args["api_key"] = get_provider_api_key(self.config.model_name) or "oauth-auth"

        # Fall back to environment variables if not using OAuth
        if not use_oauth:
            if api_key := Config.get("llm_api_key"):
                args["api_key"] = api_key
            if api_base := (
                Config.get("llm_api_base")
                or Config.get("openai_api_base")
                or Config.get("litellm_base_url")
                or Config.get("ollama_api_base")
            ):
                args["api_base"] = api_base

        if self._supports_reasoning():
            args["reasoning_effort"] = self._reasoning_effort

        return args

    def _get_chunk_content(self, chunk: Any) -> str:
        if chunk.choices and hasattr(chunk.choices[0], "delta"):
            return getattr(chunk.choices[0].delta, "content", "") or ""
        return ""

    def _extract_thinking(self, chunks: list[Any]) -> list[dict[str, Any]] | None:
        if not chunks or not self._supports_reasoning():
            return None
        try:
            resp = stream_chunk_builder(chunks)
            if resp.choices and hasattr(resp.choices[0].message, "thinking_blocks"):
                blocks: list[dict[str, Any]] = resp.choices[0].message.thinking_blocks
                return blocks
        except Exception:  # noqa: BLE001, S110  # nosec B110
            pass
        return None

    def _extract_native_tool_calls(self, response: Any) -> list[dict[str, Any]] | None:
        """Extract native tool calls from a litellm assembled response."""
        if not response or not response.choices:
            return None
        msg = response.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)
        if not tool_calls:
            return None
        result = []
        for tc in tool_calls:
            tool_name = ""
            tool_call_id = ""
            args: dict[str, Any] = {}

            try:
                function_payload = getattr(tc, "function", None)
                if isinstance(tc, dict) and not function_payload:
                    function_payload = tc.get("function")

                raw_name = getattr(function_payload, "name", "")
                if isinstance(function_payload, dict):
                    raw_name = function_payload.get("name", raw_name)
                tool_name = str(raw_name or "")

                raw_call_id = getattr(tc, "id", "")
                if isinstance(tc, dict):
                    raw_call_id = tc.get("id", raw_call_id)
                tool_call_id = str(raw_call_id or "")

                raw_args: Any = None
                if function_payload is not None:
                    raw_args = getattr(function_payload, "arguments", None)
                    if isinstance(function_payload, dict):
                        raw_args = function_payload.get("arguments", raw_args)

                if isinstance(raw_args, str):
                    parsed = json.loads(raw_args)
                    args = parsed if isinstance(parsed, dict) else {}
                elif isinstance(raw_args, dict):
                    args = raw_args
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                args = {}

            if not tool_name:
                continue

            result.append({
                "toolName": tool_name,
                "args": args,
                "tool_call_id": tool_call_id,
            })
        return result or None

    def _update_usage_stats(self, response: Any) -> None:
        try:
            if hasattr(response, "usage") and response.usage:
                input_tokens = getattr(response.usage, "prompt_tokens", 0)
                output_tokens = getattr(response.usage, "completion_tokens", 0)

                cached_tokens = 0
                if hasattr(response.usage, "prompt_tokens_details"):
                    prompt_details = response.usage.prompt_tokens_details
                    if hasattr(prompt_details, "cached_tokens"):
                        cached_tokens = prompt_details.cached_tokens or 0

            else:
                input_tokens = 0
                output_tokens = 0
                cached_tokens = 0

            # Calculate cost via our pricing DB (covers all providers)
            from esprit.llm.pricing import get_pricing_db

            cost = get_pricing_db().get_cost(
                self.config.model_name or "",
                input_tokens,
                output_tokens,
                cached_tokens,
            )

            self._total_stats.input_tokens += input_tokens
            self._total_stats.output_tokens += output_tokens
            self._total_stats.cached_tokens += cached_tokens
            self._total_stats.cost += cost
            self._total_stats.last_input_tokens = input_tokens

        except Exception:  # noqa: BLE001, S110  # nosec B110
            pass

    def _should_retry(self, e: Exception) -> bool:
        code = getattr(e, "status_code", None) or getattr(
            getattr(e, "response", None), "status_code", None
        )
        return code is None or litellm._should_retry(code)

    def _raise_error(self, e: Exception) -> None:
        from esprit.telemetry import posthog

        posthog.error("llm_error", type(e).__name__)
        raise LLMRequestFailedError(f"LLM request failed: {type(e).__name__}", str(e)) from e

    def _is_anthropic(self) -> bool:
        if not self.config.model_name:
            return False
        return any(p in self.config.model_name.lower() for p in ["anthropic/", "claude"])

    def _is_antigravity(self) -> bool:
        if not PROVIDERS_AVAILABLE or not self.config.model_name:
            return False
        model = self.config.model_name.lower()
        if model.startswith("antigravity/"):
            return True
        # If an explicit non-antigravity provider prefix is given, respect it
        if "/" in model:
            prefix = model.split("/", 1)[0]
            _NON_AG_PREFIXES = {"anthropic", "google", "openai", "bedrock",
                                "github-copilot", "gemini", "azure", "vertex_ai"}
            if prefix in _NON_AG_PREFIXES:
                return False
        # Check if the bare model name is an antigravity model and oauth is configured
        bare = model.split("/", 1)[-1]
        if bare in ANTIGRAVITY_MODELS and should_use_oauth(f"antigravity/{bare}"):
            return True
        return False

    def _try_rotate_on_rate_limit(self, e: Exception) -> bool:
        """Try to rotate accounts on a 429 rate limit error. Returns True if rotated."""
        if not PROVIDERS_AVAILABLE:
            return False
        code = getattr(e, "status_code", None) or getattr(
            getattr(e, "response", None), "status_code", None
        )
        if code != 429:
            return False

        model = self.config.model_name or ""
        # Check Antigravity routing first (mirrors _is_antigravity logic)
        if self._is_antigravity():
            provider_id = "antigravity"
        else:
            client = get_auth_client()
            provider_id = client.detect_provider(model)
        if not provider_id:
            return False

        pool = get_account_pool()
        # Read-only peek to get the current account's email (no disk write)
        current = pool.peek_best_account(provider_id)
        if not current:
            return False

        bare_model = model.split("/", 1)[-1]
        # Parse retry-after header if available
        retry_after = 60.0
        resp = getattr(e, "response", None)
        if resp is not None:
            ra = getattr(resp, "headers", {}).get("retry-after")
            if ra:
                try:
                    retry_after = float(ra)
                except ValueError:
                    pass

        pool.mark_rate_limited(provider_id, current.email, bare_model, retry_after)
        rotated = pool.rotate(provider_id, bare_model)
        if rotated:
            logger.info("Rate limited on %s, rotated to %s",
                        _mask_email(current.email), _mask_email(rotated.email))
            return True
        logger.warning("Rate limited on %s, no other accounts available",
                       _mask_email(current.email))
        return False

    def _try_model_fallback(self, e: Exception) -> bool:
        """Try switching to the next fallback model when the current one fails persistently.

        Only activates for Antigravity models when ESPRIT_AUTO_FALLBACK is not disabled.
        Returns True if successfully switched to a fallback model.
        """
        if str(Config.get("esprit_auto_fallback") or "").lower() in ("false", "0", "no"):
            return False

        if not PROVIDERS_AVAILABLE:
            return False

        from esprit.providers.antigravity import get_fallback_models

        current_model = self.config.model_name or ""
        fallbacks = get_fallback_models(current_model)

        if not fallbacks:
            return False

        # Track original model and which models have been tried this session
        if not hasattr(self, "_original_model"):
            self._original_model = current_model
        if not hasattr(self, "_tried_models"):
            self._tried_models: set[str] = set()
        self._tried_models.add(current_model.split("/", 1)[-1] if "/" in current_model else current_model)

        for fallback in fallbacks:
            if fallback in self._tried_models:
                continue

            # Switch model — intentionally sticky: the fallback becomes the
            # active model for the remainder of this LLM instance's lifetime
            # to avoid repeated failures on the original model.
            old_model = current_model
            prefix = current_model.split("/", 1)[0] + "/" if "/" in current_model else "antigravity/"
            new_model = f"{prefix}{fallback}"
            self.config.model_name = new_model
            self._tried_models.add(fallback)

            logger.warning(
                "Model %s failed (%s), falling back to %s",
                old_model, type(e).__name__, new_model,
            )
            return True

        return False

    def _supports_vision(self) -> bool:
        try:
            return bool(supports_vision(model=self.config.model_name))
        except Exception:  # noqa: BLE001
            return False

    def _supports_reasoning(self) -> bool:
        try:
            return bool(supports_reasoning(model=self.config.model_name))
        except Exception:  # noqa: BLE001
            return False

    def _strip_images(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif isinstance(item, dict) and item.get("type") == "image_url":
                        text_parts.append("[Image removed - model doesn't support vision]")
                result.append({**msg, "content": "\n".join(text_parts)})
            else:
                result.append(msg)
        return result

    def _add_cache_control(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not messages or not supports_prompt_caching(self.config.model_name):
            return messages

        result = list(messages)

        if result[0].get("role") == "system":
            content = result[0]["content"]
            result[0] = {
                **result[0],
                "content": [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]
                if isinstance(content, str)
                else content,
            }
        return result
