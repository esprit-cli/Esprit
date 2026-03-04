"""Tests for tool executor helpers."""

import asyncio
from typing import Any
from types import SimpleNamespace

import httpx
import pytest

from esprit.tools import executor as executor_module
from esprit.tools.executor import _extract_plain_result, process_tool_invocations


class TestExtractPlainResult:
    def test_uses_last_closing_result_tag(self) -> None:
        observation = (
            "<tool_result>\n"
            "<tool_name>terminal_execute</tool_name>\n"
            "<result>A literal </result> marker from tool output</result>\n"
            "</tool_result>"
        )

        parsed = _extract_plain_result(observation, "terminal_execute")
        assert parsed == "A literal </result> marker from tool output"

    def test_returns_original_when_result_tags_missing(self) -> None:
        observation = "plain text without XML wrapper"
        parsed = _extract_plain_result(observation, "terminal_execute")
        assert parsed == observation

    def test_extracts_via_xml_parser_for_wellformed_xml(self) -> None:
        observation = (
            "<tool_result>"
            "<tool_name>terminal_execute</tool_name>"
            "<result>hello world</result>"
            "</tool_result>"
        )
        parsed = _extract_plain_result(observation, "terminal_execute")
        assert parsed == "hello world"

    def test_preserves_nested_result_payload(self) -> None:
        observation = (
            "<tool_result>"
            "<tool_name>terminal_execute</tool_name>"
            "<result>prefix <b>value</b> suffix</result>"
            "</tool_result>"
        )
        parsed = _extract_plain_result(observation, "terminal_execute")
        assert parsed == "prefix <b>value</b> suffix"

    def test_falls_back_to_string_search_for_malformed_xml(self) -> None:
        # Ampersand without escaping makes this invalid XML for ElementTree
        observation = (
            "<tool_result>\n"
            "<tool_name>http_request</tool_name>\n"
            "<result>foo & bar</result>\n"
            "</tool_result>"
        )
        parsed = _extract_plain_result(observation, "http_request")
        assert parsed == "foo & bar"


class TestProcessToolInvocations:
    def test_mixed_tool_call_ids_fall_back_to_legacy_mode(self, monkeypatch: Any) -> None:
        async def fake_execute_single_tool(
            tool_inv: dict[str, Any],
            agent_state: Any,
            tracer: Any,
            agent_id: str,
        ) -> tuple[str, list[dict[str, Any]], bool]:
            tool_name = str(tool_inv.get("toolName") or "unknown")
            observation_xml = (
                "<tool_result>\n"
                f"<tool_name>{tool_name}</tool_name>\n"
                f"<result>{tool_name} ok</result>\n"
                "</tool_result>"
            )
            return observation_xml, [], False

        monkeypatch.setattr(executor_module, "_execute_single_tool", fake_execute_single_tool)

        conversation_history: list[dict[str, Any]] = []
        tool_invocations = [
            {"toolName": "first", "args": {}, "tool_call_id": "call_1"},
            {"toolName": "second", "args": {}},
        ]

        should_finish = asyncio.run(process_tool_invocations(tool_invocations, conversation_history))

        assert should_finish is False
        assert len(conversation_history) == 1
        assert conversation_history[0]["role"] == "user"
        assert isinstance(conversation_history[0]["content"], str)
        assert "first ok" in conversation_history[0]["content"]
        assert "second ok" in conversation_history[0]["content"]

    def test_all_tool_call_ids_use_native_mode(self, monkeypatch: Any) -> None:
        async def fake_execute_single_tool(
            tool_inv: dict[str, Any],
            agent_state: Any,
            tracer: Any,
            agent_id: str,
        ) -> tuple[str, list[dict[str, Any]], bool]:
            tool_name = str(tool_inv.get("toolName") or "unknown")
            observation_xml = (
                "<tool_result>\n"
                f"<tool_name>{tool_name}</tool_name>\n"
                f"<result>{tool_name} ok</result>\n"
                "</tool_result>"
            )
            return observation_xml, [], False

        monkeypatch.setattr(executor_module, "_execute_single_tool", fake_execute_single_tool)

        conversation_history: list[dict[str, Any]] = []
        tool_invocations = [
            {"toolName": "first", "args": {}, "tool_call_id": "call_1"},
            {"toolName": "second", "args": {}, "tool_call_id": "call_2"},
        ]

        should_finish = asyncio.run(process_tool_invocations(tool_invocations, conversation_history))

        assert should_finish is False
        assert len(conversation_history) == 2
        assert conversation_history[0]["role"] == "tool"
        assert conversation_history[0]["tool_call_id"] == "call_1"
        assert conversation_history[1]["role"] == "tool"
        assert conversation_history[1]["tool_call_id"] == "call_2"


class TestSandboxExecuteRetries:
    @pytest.mark.asyncio
    async def test_retries_transient_http_status_before_succeeding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeRuntime:
            async def get_sandbox_url(self, _sandbox_id: str, _tool_server_port: int) -> str:
                return "https://tool.example.test"

        class FakeResponse:
            def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
                self.status_code = status_code
                self._payload = payload
                self.request = httpx.Request("POST", "https://tool.example.test/execute")

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"HTTP {self.status_code}",
                        request=self.request,
                        response=self,
                    )

            def json(self) -> dict[str, Any]:
                return self._payload

        state: dict[str, int] = {"calls": 0}
        responses = [
            FakeResponse(503, {}),
            FakeResponse(200, {"result": "ok"}),
        ]

        class FakeAsyncClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                _ = (args, kwargs)

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(
                self,
                _exc_type: type[BaseException] | None,
                _exc: BaseException | None,
                _tb: object,
            ) -> None:
                return None

            async def post(
                self,
                _url: str,
                *,
                json: dict[str, Any],
                headers: dict[str, str],
                timeout: httpx.Timeout,
            ) -> FakeResponse:
                _ = (json, headers, timeout)
                state["calls"] += 1
                return responses.pop(0)

        async def _no_sleep(_seconds: float) -> None:
            return None

        monkeypatch.setattr(executor_module, "get_runtime", lambda: FakeRuntime())
        monkeypatch.setattr(executor_module.httpx, "AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(executor_module.asyncio, "sleep", _no_sleep)
        monkeypatch.setattr(executor_module.posthog, "error", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(executor_module, "_EXECUTE_MAX_ATTEMPTS", 3)

        agent_state = SimpleNamespace(
            sandbox_id="sandbox-1",
            sandbox_token="token-1",
            sandbox_info={"tool_server_port": 443},
            agent_id="agent-1",
        )

        result = await executor_module._execute_tool_in_sandbox(
            "terminal_execute",
            agent_state,
            command="pwd",
        )

        assert result == "ok"
        assert state["calls"] == 2

    @pytest.mark.asyncio
    async def test_does_not_retry_authentication_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class FakeRuntime:
            async def get_sandbox_url(self, _sandbox_id: str, _tool_server_port: int) -> str:
                return "https://tool.example.test"

        class FakeResponse:
            status_code = 401
            request = httpx.Request("POST", "https://tool.example.test/execute")

            def raise_for_status(self) -> None:
                raise httpx.HTTPStatusError("HTTP 401", request=self.request, response=self)

            @staticmethod
            def json() -> dict[str, Any]:
                return {}

        state: dict[str, int] = {"calls": 0}

        class FakeAsyncClient:
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                _ = (args, kwargs)

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(
                self,
                _exc_type: type[BaseException] | None,
                _exc: BaseException | None,
                _tb: object,
            ) -> None:
                return None

            async def post(
                self,
                _url: str,
                *,
                json: dict[str, Any],
                headers: dict[str, str],
                timeout: httpx.Timeout,
            ) -> FakeResponse:
                _ = (json, headers, timeout)
                state["calls"] += 1
                return FakeResponse()

        monkeypatch.setattr(executor_module, "get_runtime", lambda: FakeRuntime())
        monkeypatch.setattr(executor_module.httpx, "AsyncClient", FakeAsyncClient)
        monkeypatch.setattr(executor_module.posthog, "error", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(executor_module, "_EXECUTE_MAX_ATTEMPTS", 5)

        agent_state = SimpleNamespace(
            sandbox_id="sandbox-1",
            sandbox_token="token-1",
            sandbox_info={"tool_server_port": 443},
            agent_id="agent-1",
        )

        with pytest.raises(RuntimeError, match="Authentication failed"):
            await executor_module._execute_tool_in_sandbox(
                "terminal_execute",
                agent_state,
                command="pwd",
            )

        assert state["calls"] == 1
