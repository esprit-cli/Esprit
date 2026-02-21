"""Tests for tool executor helpers."""

import asyncio
from typing import Any

import httpx

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


class _DummyAgentState:
    def __init__(self) -> None:
        self.sandbox_id = "sandbox-1"
        self.sandbox_token = "token-1"
        self.sandbox_info = {"tool_server_port": 48081}
        self.agent_id = "agent-1"


class _DummyRuntime:
    def __init__(self) -> None:
        self.revive_calls = 0

    async def get_sandbox_url(self, container_id: str, port: int) -> str:
        assert container_id == "sandbox-1"
        assert port == 48081
        return "http://sandbox.local"

    async def revive_sandbox(self, _container_id: str) -> dict[str, Any]:
        self.revive_calls += 1
        return {
            "workspace_id": "sandbox-1",
            "api_url": "http://sandbox.local",
            "auth_token": "token-2",
            "tool_server_port": 48081,
        }


class _FakeResponse:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", "http://sandbox.local/execute")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                message=f"HTTP {self.status_code}",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self) -> dict[str, Any]:
        return self._payload


class TestSandboxExecutionRetries:
    def test_retries_transient_request_errors_then_succeeds(self, monkeypatch: Any) -> None:
        runtime = _DummyRuntime()
        agent_state = _DummyAgentState()
        calls = {"count": 0}

        class _FakeClient:
            async def __aenter__(self) -> "_FakeClient":
                return self

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                return None

            async def post(self, *args: Any, **kwargs: Any) -> _FakeResponse:
                calls["count"] += 1
                if calls["count"] < 3:
                    raise httpx.ReadTimeout(
                        "timed out",
                        request=httpx.Request("POST", "http://sandbox.local/execute"),
                    )
                return _FakeResponse({"result": {"ok": True}})

        monkeypatch.setattr(executor_module, "get_runtime", lambda: runtime)
        monkeypatch.setattr(executor_module.httpx, "AsyncClient", lambda **_: _FakeClient())

        result = asyncio.run(
            executor_module._execute_tool_in_sandbox(
                "terminal_execute",
                agent_state,
                command="echo test",
            )
        )

        assert result == {"ok": True}
        assert calls["count"] == 3
        assert runtime.revive_calls >= 1

    def test_retries_busy_tool_server_error_then_succeeds(self, monkeypatch: Any) -> None:
        runtime = _DummyRuntime()
        agent_state = _DummyAgentState()
        calls = {"count": 0}

        class _FakeClient:
            async def __aenter__(self) -> "_FakeClient":
                return self

            async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
                return None

            async def post(self, *args: Any, **kwargs: Any) -> _FakeResponse:
                calls["count"] += 1
                if calls["count"] == 1:
                    return _FakeResponse({"error": "Agent has an active tool request; retry shortly"})
                return _FakeResponse({"result": {"ok": True}})

        monkeypatch.setattr(executor_module, "get_runtime", lambda: runtime)
        monkeypatch.setattr(executor_module.httpx, "AsyncClient", lambda **_: _FakeClient())

        result = asyncio.run(
            executor_module._execute_tool_in_sandbox(
                "python_action",
                agent_state,
                action="list_sessions",
            )
        )

        assert result == {"ok": True}
        assert calls["count"] == 2
