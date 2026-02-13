"""Tests for tool executor helpers."""

import asyncio
from typing import Any

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
