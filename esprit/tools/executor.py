import asyncio
import inspect
import os
from typing import Any

import defusedxml.ElementTree as DefusedET

import httpx

from esprit.config import Config
from esprit.telemetry import posthog


if os.getenv("ESPRIT_SANDBOX_MODE", "false").lower() == "false":
    from esprit.runtime import get_runtime

from .argument_parser import convert_arguments
from .registry import (
    get_tool_by_name,
    get_tool_names,
    get_tool_param_schema,
    needs_agent_state,
    should_execute_in_sandbox,
)


_SERVER_TIMEOUT = float(Config.get("esprit_sandbox_execution_timeout") or "300")
_CLIENT_GRACE_TIMEOUT = float(Config.get("esprit_sandbox_client_grace_timeout") or "120")
SANDBOX_EXECUTION_TIMEOUT = _SERVER_TIMEOUT + max(30.0, _CLIENT_GRACE_TIMEOUT)
SANDBOX_CONNECT_TIMEOUT = float(Config.get("esprit_sandbox_connect_timeout") or "10")
SANDBOX_REQUEST_RETRIES = max(0, int(Config.get("esprit_sandbox_request_retries") or "2"))
SANDBOX_RETRY_BASE_DELAY_MS = max(
    100,
    int(Config.get("esprit_sandbox_retry_base_delay_ms") or "400"),
)


def _compute_tool_request_timeout(kwargs: dict[str, Any]) -> httpx.Timeout:
    tool_timeout = kwargs.get("timeout")
    tool_timeout_s = 0.0
    if isinstance(tool_timeout, (int, float)):
        tool_timeout_s = max(0.0, float(tool_timeout))
    total_timeout = max(SANDBOX_EXECUTION_TIMEOUT, tool_timeout_s + 120.0)
    return httpx.Timeout(timeout=total_timeout, connect=SANDBOX_CONNECT_TIMEOUT)


def _is_retryable_request_error(error: httpx.RequestError) -> bool:
    return isinstance(
        error,
        (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadError,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.WriteError,
        ),
    )


def _is_retryable_tool_server_error(error_msg: str) -> bool:
    message = error_msg.lower()
    return (
        "active tool request" in message
        or "cancelled by newer request" in message
        or "tool timed out after" in message
    )


async def _recover_sandbox_connectivity(runtime: Any, agent_state: Any) -> bool:
    revive = getattr(runtime, "revive_sandbox", None)
    if not callable(revive):
        return False

    try:
        revived_info = await revive(agent_state.sandbox_id)
    except Exception:  # noqa: BLE001
        return False

    if not isinstance(revived_info, dict):
        return False

    token = revived_info.get("auth_token")
    port = revived_info.get("tool_server_port")
    if isinstance(token, str) and token:
        agent_state.sandbox_token = token
    if isinstance(port, int) and hasattr(agent_state, "sandbox_info"):
        agent_state.sandbox_info["tool_server_port"] = port
    return True


async def _retry_delay(attempt: int) -> None:
    delay_s = min(8.0, (SANDBOX_RETRY_BASE_DELAY_MS / 1000.0) * (2**attempt))
    await asyncio.sleep(delay_s)


async def execute_tool(tool_name: str, agent_state: Any | None = None, **kwargs: Any) -> Any:
    execute_in_sandbox = should_execute_in_sandbox(tool_name)
    sandbox_mode = os.getenv("ESPRIT_SANDBOX_MODE", "false").lower() == "true"

    if execute_in_sandbox and not sandbox_mode:
        return await _execute_tool_in_sandbox(tool_name, agent_state, **kwargs)

    return await _execute_tool_locally(tool_name, agent_state, **kwargs)


async def _execute_tool_in_sandbox(tool_name: str, agent_state: Any, **kwargs: Any) -> Any:
    if not hasattr(agent_state, "sandbox_id") or not agent_state.sandbox_id:
        raise ValueError("Agent state with a valid sandbox_id is required for sandbox execution.")

    if not hasattr(agent_state, "sandbox_token") or not agent_state.sandbox_token:
        raise ValueError(
            "Agent state with a valid sandbox_token is required for sandbox execution."
        )

    if (
        not hasattr(agent_state, "sandbox_info")
        or "tool_server_port" not in agent_state.sandbox_info
    ):
        raise ValueError(
            "Agent state with a valid sandbox_info containing tool_server_port is required."
        )

    runtime = get_runtime()

    agent_id = getattr(agent_state, "agent_id", "unknown")

    request_data = {
        "agent_id": agent_id,
        "tool_name": tool_name,
        "kwargs": kwargs,
    }

    timeout = _compute_tool_request_timeout(kwargs)
    max_attempts = SANDBOX_REQUEST_RETRIES + 1

    async with httpx.AsyncClient(trust_env=False) as client:
        for attempt in range(max_attempts):
            try:
                tool_server_port = agent_state.sandbox_info["tool_server_port"]
                server_url = await runtime.get_sandbox_url(agent_state.sandbox_id, tool_server_port)
                request_url = f"{server_url}/execute"
                headers = {
                    "Authorization": f"Bearer {agent_state.sandbox_token}",
                    "Content-Type": "application/json",
                }

                response = await client.post(
                    request_url,
                    json=request_data,
                    headers=headers,
                    timeout=timeout,
                )
                response.raise_for_status()
                response_data = response.json()
                if response_data.get("error"):
                    error_msg = str(response_data["error"])
                    if (
                        attempt < max_attempts - 1
                        and _is_retryable_tool_server_error(error_msg)
                    ):
                        await _retry_delay(attempt)
                        continue
                    posthog.error("tool_execution_error", f"{tool_name}: {error_msg}")
                    raise RuntimeError(f"Sandbox execution error: {error_msg}")
                return response_data.get("result")
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                posthog.error("tool_http_error", f"{tool_name}: HTTP {status_code}")
                if status_code == 401:
                    if attempt < max_attempts - 1 and await _recover_sandbox_connectivity(
                        runtime, agent_state
                    ):
                        await _retry_delay(attempt)
                        continue
                    raise RuntimeError(
                        "Authentication failed: Invalid or missing sandbox token"
                    ) from e
                if attempt < max_attempts - 1 and status_code in (408, 429, 500, 502, 503, 504):
                    await _retry_delay(attempt)
                    continue
                raise RuntimeError(f"HTTP error calling tool server: {status_code}") from e
            except httpx.RequestError as e:
                error_type = type(e).__name__
                is_retryable = _is_retryable_request_error(e)
                if attempt < max_attempts - 1 and is_retryable:
                    await _recover_sandbox_connectivity(runtime, agent_state)
                    await _retry_delay(attempt)
                    continue
                posthog.error("tool_request_error", f"{tool_name}: {error_type}")
                raise RuntimeError(f"Request error calling tool server: {error_type}") from e

    raise RuntimeError("Tool execution failed after retry attempts")


async def _execute_tool_locally(tool_name: str, agent_state: Any | None, **kwargs: Any) -> Any:
    tool_func = get_tool_by_name(tool_name)
    if not tool_func:
        raise ValueError(f"Tool '{tool_name}' not found")

    converted_kwargs = convert_arguments(tool_func, kwargs)

    if needs_agent_state(tool_name):
        if agent_state is None:
            raise ValueError(f"Tool '{tool_name}' requires agent_state but none was provided.")
        result = tool_func(agent_state=agent_state, **converted_kwargs)
    else:
        result = tool_func(**converted_kwargs)

    return await result if inspect.isawaitable(result) else result


def validate_tool_availability(tool_name: str | None) -> tuple[bool, str]:
    if tool_name is None:
        available = ", ".join(sorted(get_tool_names()))
        return False, f"Tool name is missing. Available tools: {available}"

    if tool_name not in get_tool_names():
        available = ", ".join(sorted(get_tool_names()))
        return False, f"Tool '{tool_name}' is not available. Available tools: {available}"

    return True, ""


def _validate_tool_arguments(tool_name: str, kwargs: dict[str, Any]) -> str | None:
    param_schema = get_tool_param_schema(tool_name)
    if not param_schema or not param_schema.get("has_params"):
        return None

    allowed_params: set[str] = param_schema.get("params", set())
    required_params: set[str] = param_schema.get("required", set())
    optional_params = allowed_params - required_params

    schema_hint = _format_schema_hint(tool_name, required_params, optional_params)

    unknown_params = set(kwargs.keys()) - allowed_params
    if unknown_params:
        unknown_list = ", ".join(sorted(unknown_params))
        return f"Tool '{tool_name}' received unknown parameter(s): {unknown_list}\n{schema_hint}"

    missing_required = [
        param for param in required_params if param not in kwargs or kwargs.get(param) in (None, "")
    ]
    if missing_required:
        missing_list = ", ".join(sorted(missing_required))
        return f"Tool '{tool_name}' missing required parameter(s): {missing_list}\n{schema_hint}"

    return None


def _format_schema_hint(tool_name: str, required: set[str], optional: set[str]) -> str:
    parts = [f"Valid parameters for '{tool_name}':"]
    if required:
        parts.append(f"  Required: {', '.join(sorted(required))}")
    if optional:
        parts.append(f"  Optional: {', '.join(sorted(optional))}")
    return "\n".join(parts)


async def execute_tool_with_validation(
    tool_name: str | None, agent_state: Any | None = None, **kwargs: Any
) -> Any:
    is_valid, error_msg = validate_tool_availability(tool_name)
    if not is_valid:
        return f"Error: {error_msg}"

    assert tool_name is not None

    arg_error = _validate_tool_arguments(tool_name, kwargs)
    if arg_error:
        return f"Error: {arg_error}"

    try:
        result = await execute_tool(tool_name, agent_state, **kwargs)
    except Exception as e:  # noqa: BLE001
        error_str = str(e)
        if len(error_str) > 500:
            error_str = error_str[:500] + "... [truncated]"
        return f"Error executing {tool_name}: {error_str}"
    else:
        return result


async def execute_tool_invocation(tool_inv: dict[str, Any], agent_state: Any | None = None) -> Any:
    tool_name = tool_inv.get("toolName")
    tool_args = tool_inv.get("args", {})

    return await execute_tool_with_validation(tool_name, agent_state, **tool_args)


def _check_error_result(result: Any) -> tuple[bool, Any]:
    is_error = False
    error_payload: Any = None

    if (isinstance(result, dict) and "error" in result) or (
        isinstance(result, str) and result.strip().lower().startswith("error:")
    ):
        is_error = True
        error_payload = result

    return is_error, error_payload


def _update_tracer_with_result(
    tracer: Any, execution_id: Any, is_error: bool, result: Any, error_payload: Any
) -> None:
    if not tracer or not execution_id:
        return

    try:
        if is_error:
            tracer.update_tool_execution(execution_id, "error", error_payload)
        else:
            tracer.update_tool_execution(execution_id, "completed", result)
    except (ConnectionError, RuntimeError) as e:
        error_msg = str(e)
        if tracer and execution_id:
            tracer.update_tool_execution(execution_id, "error", error_msg)
        raise


def _format_tool_result(tool_name: str, result: Any) -> tuple[str, list[dict[str, Any]]]:
    images: list[dict[str, Any]] = []

    screenshot_data = extract_screenshot_from_result(result)
    if screenshot_data:
        images.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{screenshot_data}"},
            }
        )
        result_str = remove_screenshot_from_result(result)
    else:
        result_str = result

    if result_str is None:
        final_result_str = f"Tool {tool_name} executed successfully"
    else:
        final_result_str = str(result_str)
        if len(final_result_str) > 10000:
            start_part = final_result_str[:4000]
            end_part = final_result_str[-4000:]
            final_result_str = start_part + "\n\n... [middle content truncated] ...\n\n" + end_part

    observation_xml = (
        f"<tool_result>\n<tool_name>{tool_name}</tool_name>\n"
        f"<result>{final_result_str}</result>\n</tool_result>"
    )

    return observation_xml, images


async def _execute_single_tool(
    tool_inv: dict[str, Any],
    agent_state: Any | None,
    tracer: Any | None,
    agent_id: str,
) -> tuple[str, list[dict[str, Any]], bool]:
    tool_name = tool_inv.get("toolName", "unknown")
    args = tool_inv.get("args", {})
    execution_id = None
    should_agent_finish = False

    if tracer:
        execution_id = tracer.log_tool_execution_start(agent_id, tool_name, args)

    try:
        result = await execute_tool_invocation(tool_inv, agent_state)

        is_error, error_payload = _check_error_result(result)

        if (
            tool_name in ("finish_scan", "agent_finish")
            and not is_error
            and isinstance(result, dict)
        ):
            if tool_name == "finish_scan":
                should_agent_finish = result.get("scan_completed", False)
            elif tool_name == "agent_finish":
                should_agent_finish = result.get("agent_completed", False)

        _update_tracer_with_result(tracer, execution_id, is_error, result, error_payload)

    except (ConnectionError, RuntimeError, ValueError, TypeError, OSError) as e:
        error_msg = str(e)
        if tracer and execution_id:
            tracer.update_tool_execution(execution_id, "error", error_msg)
        raise

    observation_xml, images = _format_tool_result(tool_name, result)
    return observation_xml, images, should_agent_finish


def _get_tracer_and_agent_id(agent_state: Any | None) -> tuple[Any | None, str]:
    try:
        from esprit.telemetry.tracer import get_global_tracer

        tracer = get_global_tracer()
        agent_id = agent_state.agent_id if agent_state else "unknown_agent"
    except (ImportError, AttributeError):
        tracer = None
        agent_id = "unknown_agent"

    return tracer, agent_id


async def process_tool_invocations(
    tool_invocations: list[dict[str, Any]],
    conversation_history: list[dict[str, Any]],
    agent_state: Any | None = None,
) -> bool:
    observation_parts: list[str] = []
    all_images: list[dict[str, Any]] = []
    should_agent_finish = False

    tracer, agent_id = _get_tracer_and_agent_id(agent_state)

    # Native mode requires every invocation to carry a tool_call_id.
    # Mixed payloads (some with IDs, some without) fall back to legacy XML mode.
    tool_call_ids = [str(inv.get("tool_call_id") or "") for inv in tool_invocations]
    has_all_tool_call_ids = bool(tool_call_ids) and all(tool_call_ids)
    has_mixed_tool_call_ids = any(tool_call_ids) and not has_all_tool_call_ids
    is_native = has_all_tool_call_ids
    if has_mixed_tool_call_ids:
        is_native = False

    for tool_inv in tool_invocations:
        observation_xml, images, tool_should_finish = await _execute_single_tool(
            tool_inv, agent_state, tracer, agent_id
        )

        if is_native:
            # Native mode: each result is a separate role=tool message
            tool_call_id = str(tool_inv.get("tool_call_id") or "")
            tool_name = tool_inv.get("toolName", "unknown")
            # Extract plain result text (strip XML wrapper)
            result_text = _extract_plain_result(observation_xml, tool_name)
            if images:
                content: list[dict[str, Any]] = [{"type": "text", "text": result_text}]
                content.extend(images)
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": content,
                })
            else:
                conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_text,
                })
        else:
            observation_parts.append(observation_xml)
            all_images.extend(images)

        if tool_should_finish:
            should_agent_finish = True

    # Legacy XML mode: combine all results into one user message
    if not is_native and observation_parts:
        if all_images:
            content = [{"type": "text", "text": "Tool Results:\n\n" + "\n\n".join(observation_parts)}]
            content.extend(all_images)
            conversation_history.append({"role": "user", "content": content})
        else:
            observation_content = "Tool Results:\n\n" + "\n\n".join(observation_parts)
            conversation_history.append({"role": "user", "content": observation_content})

    return should_agent_finish


def _extract_result_from_string(observation_xml: str) -> str | None:
    start = observation_xml.find("<result>")
    end = observation_xml.rfind("</result>")
    if start == -1 or end == -1 or end < start + len("<result>"):
        return None
    return observation_xml[start + len("<result>") : end]


def _extract_plain_result(observation_xml: str, _tool_name: str) -> str:
    """Extract plain result text from XML-wrapped tool result."""
    try:
        root = DefusedET.fromstring(observation_xml)
        if root.find("result") is not None:
            # Keep raw payload to preserve nested tags and trailing text.
            extracted = _extract_result_from_string(observation_xml)
            if extracted is not None:
                return extracted

            # Should be rare: parser found <result> but raw tag search failed.
            result_node = root.find("result")
            if result_node is not None:
                return "".join(result_node.itertext()).strip()
    except DefusedET.ParseError:
        pass

    extracted = _extract_result_from_string(observation_xml)
    if extracted is not None:
        return extracted

    return observation_xml


def extract_screenshot_from_result(result: Any) -> str | None:
    if not isinstance(result, dict):
        return None

    screenshot = result.get("screenshot")
    if isinstance(screenshot, str) and screenshot:
        return screenshot

    return None


def remove_screenshot_from_result(result: Any) -> Any:
    if not isinstance(result, dict):
        return result

    result_copy = result.copy()
    if "screenshot" in result_copy:
        result_copy["screenshot"] = "[Image data extracted - see attached image]"

    return result_copy
