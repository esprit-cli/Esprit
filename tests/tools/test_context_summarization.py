"""Tests for subagent inherited context summarization."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from esprit.tools.agents_graph.agents_graph_actions import (
    _RECENT_MESSAGES_TO_KEEP,
    _format_messages_as_text,
    _format_messages_brief,
    _summarize_inherited_context,
)


def _make_messages(count: int) -> list[dict[str, Any]]:
    """Build a synthetic conversation history with *count* messages."""
    msgs: list[dict[str, Any]] = []
    for i in range(count):
        role = "user" if i % 2 == 0 else "assistant"
        msgs.append({"role": role, "content": f"message {i}"})
    return msgs


# ── _format_messages_as_text ─────────────────────────────────────


class TestFormatMessagesAsText:
    def test_basic(self) -> None:
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        result = _format_messages_as_text(msgs)
        assert "user: hello" in result
        assert "assistant: world" in result

    def test_skips_empty(self) -> None:
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "  "},
        ]
        result = _format_messages_as_text(msgs)
        assert result == "user: hello"

    def test_tool_messages(self) -> None:
        msgs = [
            {"role": "tool", "content": "scan result", "tool_call_id": "call_abc"},
        ]
        result = _format_messages_as_text(msgs)
        assert "tool_result(call_abc): scan result" in result

    def test_tool_calls_label(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": "running scan",
                "tool_calls": [
                    {"function": {"name": "nmap_scan"}},
                    {"function": {"name": "nikto_scan"}},
                ],
            },
        ]
        result = _format_messages_as_text(msgs)
        assert "assistant [called: nmap_scan, nikto_scan]:" in result


# ── _format_messages_brief ───────────────────────────────────────


class TestFormatMessagesBrief:
    def test_short_messages_unchanged(self) -> None:
        msgs = [{"role": "user", "content": "short"}]
        result = _format_messages_brief(msgs)
        assert result == "user: short"

    def test_long_messages_truncated(self) -> None:
        content = "a" * 1000
        msgs = [{"role": "assistant", "content": content}]
        result = _format_messages_brief(msgs)
        assert "...[truncated]..." in result
        assert len(result) < len(content)


# ── _summarize_inherited_context ─────────────────────────────────


class TestSummarizeInheritedContext:
    def test_uses_llm_summary_when_available(self) -> None:
        msgs = _make_messages(20)
        llm_summary = "LLM generated summary of old context"

        mock_response = {"role": "assistant", "content": llm_summary}
        with (
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                return_value=mock_response,
            ) as mock_summarize,
            patch(
                "esprit.config.Config"
            ) as mock_config,
        ):
            mock_config.get.return_value = "test-model"
            result = _summarize_inherited_context(msgs, "test task")

        mock_summarize.assert_called_once()
        assert llm_summary in result
        assert "<earlier_context_summary" in result
        assert "<recent_parent_activity>" in result

    def test_fallback_to_brief_on_llm_failure(self) -> None:
        msgs = _make_messages(20)

        with (
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                side_effect=RuntimeError("LLM unavailable"),
            ),
            patch(
                "esprit.config.Config"
            ) as mock_config,
        ):
            mock_config.get.return_value = "test-model"
            result = _summarize_inherited_context(msgs, "test task")

        # Should still return valid output using fallback
        assert "<earlier_context_summary" in result
        assert "<recent_parent_activity>" in result

    def test_fallback_when_llm_returns_empty(self) -> None:
        msgs = _make_messages(20)

        mock_response = {"role": "assistant", "content": ""}
        with (
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                return_value=mock_response,
            ),
            patch(
                "esprit.config.Config"
            ) as mock_config,
        ):
            mock_config.get.return_value = "test-model"
            result = _summarize_inherited_context(msgs, "test task")

        # Should fall back to brief formatting
        assert "<earlier_context_summary" in result
        # Brief format includes role labels
        assert "user:" in result or "assistant:" in result

    def test_recent_messages_preserved(self) -> None:
        msgs = _make_messages(25)

        mock_response = {"role": "assistant", "content": "summary of old stuff"}
        with (
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                return_value=mock_response,
            ),
            patch(
                "esprit.config.Config"
            ) as mock_config,
        ):
            mock_config.get.return_value = "test-model"
            result = _summarize_inherited_context(msgs, "test task")

        # Last 10 messages should be in the recent section
        for i in range(25 - _RECENT_MESSAGES_TO_KEEP, 25):
            assert f"message {i}" in result

    def test_old_messages_sent_to_summarizer(self) -> None:
        msgs = _make_messages(25)
        expected_old_count = 25 - _RECENT_MESSAGES_TO_KEEP

        mock_response = {"role": "assistant", "content": "summary"}
        with (
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                return_value=mock_response,
            ) as mock_summarize,
            patch(
                "esprit.config.Config"
            ) as mock_config,
        ):
            mock_config.get.return_value = "test-model"
            _summarize_inherited_context(msgs, "test task")

        # Should pass the old messages (not the recent ones) to summarizer
        call_args = mock_summarize.call_args
        old_msgs_passed = call_args[0][0]
        assert len(old_msgs_passed) == expected_old_count


# ── _run_agent_in_thread context branching ───────────────────────


class TestRunAgentInThreadContextBranching:
    """Test that _run_agent_in_thread uses the correct path based on history length."""

    def test_short_history_uses_individual_messages(self) -> None:
        """Short histories (<= threshold) should be passed as individual messages."""
        state = MagicMock()
        state.task = "test task"
        state.agent_id = "agent_123"
        state.parent_id = "agent_parent"
        state.agent_name = "Test Agent"

        msgs = _make_messages(5)  # Well under threshold

        from esprit.tools.agents_graph import agents_graph_actions as mod

        mod._agent_graph["nodes"]["agent_parent"] = {"name": "Parent", "task": "parent task"}
        mod._agent_graph["nodes"]["agent_123"] = {
            "name": "Test Agent",
            "task": "test task",
            "status": "running",
            "parent_id": "agent_parent",
        }

        # Verify _summarize_inherited_context is NOT called for short histories
        with patch.object(mod, "_summarize_inherited_context") as mock_summarize:
            agent = MagicMock()

            try:
                mod._run_agent_in_thread(agent, state, msgs)
            except Exception:
                pass

            mock_summarize.assert_not_called()

        # Clean up
        mod._agent_graph["nodes"].pop("agent_123", None)
        mod._agent_graph["nodes"].pop("agent_parent", None)

    def test_long_history_triggers_summarization(self) -> None:
        """Long histories (> threshold) should trigger summarization."""
        state = MagicMock()
        state.task = "test task"
        state.agent_id = "agent_456"
        state.parent_id = "agent_parent"
        state.agent_name = "Test Agent"

        msgs = _make_messages(20)  # Over threshold

        from esprit.tools.agents_graph import agents_graph_actions as mod

        mod._agent_graph["nodes"]["agent_parent"] = {"name": "Parent", "task": "parent task"}
        mod._agent_graph["nodes"]["agent_456"] = {
            "name": "Test Agent",
            "task": "test task",
            "status": "running",
            "parent_id": "agent_parent",
        }

        with patch.object(
            mod,
            "_summarize_inherited_context",
            return_value="summarized context",
        ) as mock_summarize:
            agent = MagicMock()

            try:
                mod._run_agent_in_thread(agent, state, msgs)
            except Exception:
                pass

            mock_summarize.assert_called_once_with(msgs, state.task)

        # Clean up
        mod._agent_graph["nodes"].pop("agent_456", None)
        mod._agent_graph["nodes"].pop("agent_parent", None)
