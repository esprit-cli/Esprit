"""Tests for subagent inherited context summarization."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from esprit.agents.state import AgentState
from esprit.tools.agents_graph.agents_graph_actions import (
    _RECENT_MESSAGES_TO_KEEP,
    _agent_graph,
    _agent_instances,
    _agent_messages,
    _agent_states,
    _running_agents,
    _format_messages_as_text,
    _format_messages_brief,
    _snapshot_inherited_messages,
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
    def test_defaults_to_local_summary_without_llm_call(self) -> None:
        msgs = _make_messages(20)

        with patch("esprit.llm.memory_compressor.summarize_messages") as mock_summarize:
            result = _summarize_inherited_context(msgs, "test task")

        mock_summarize.assert_not_called()
        assert "<earlier_context_summary" in result
        assert "<recent_parent_activity>" in result

    def test_uses_llm_summary_when_available(self) -> None:
        msgs = _make_messages(20)
        llm_summary = "LLM generated summary of old context"

        mock_response = {"role": "assistant", "content": llm_summary}
        with (
            patch(
                "esprit.tools.agents_graph.agents_graph_actions._env_flag",
                return_value=True,
            ),
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                return_value=mock_response,
            ) as mock_summarize,
            patch(
                "esprit.tools.agents_graph.agents_graph_actions.Config.get",
                side_effect=lambda name: "test-model" if name == "esprit_llm" else None,
            ),
        ):
            result = _summarize_inherited_context(msgs, "test task")

        mock_summarize.assert_called_once()
        assert llm_summary in result
        assert "<earlier_context_summary" in result
        assert "<recent_parent_activity>" in result

    def test_fallback_to_brief_on_llm_failure(self) -> None:
        msgs = _make_messages(20)

        with (
            patch(
                "esprit.tools.agents_graph.agents_graph_actions._env_flag",
                return_value=True,
            ),
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                side_effect=RuntimeError("LLM unavailable"),
            ),
            patch(
                "esprit.tools.agents_graph.agents_graph_actions.Config.get",
                side_effect=lambda name: "test-model" if name == "esprit_llm" else None,
            ),
        ):
            result = _summarize_inherited_context(msgs, "test task")

        # Should still return valid output using fallback
        assert "<earlier_context_summary" in result
        assert "<recent_parent_activity>" in result

    def test_fallback_when_llm_returns_empty(self) -> None:
        msgs = _make_messages(20)

        mock_response = {"role": "assistant", "content": ""}
        with (
            patch(
                "esprit.tools.agents_graph.agents_graph_actions._env_flag",
                return_value=True,
            ),
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                return_value=mock_response,
            ),
            patch(
                "esprit.tools.agents_graph.agents_graph_actions.Config.get",
                side_effect=lambda name: "test-model" if name == "esprit_llm" else None,
            ),
        ):
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
                "esprit.tools.agents_graph.agents_graph_actions._env_flag",
                return_value=True,
            ),
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                return_value=mock_response,
            ),
            patch(
                "esprit.tools.agents_graph.agents_graph_actions.Config.get",
                side_effect=lambda name: "test-model" if name == "esprit_llm" else None,
            ),
        ):
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
                "esprit.tools.agents_graph.agents_graph_actions._env_flag",
                return_value=True,
            ),
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                return_value=mock_response,
            ) as mock_summarize,
            patch(
                "esprit.tools.agents_graph.agents_graph_actions.Config.get",
                side_effect=lambda name: "test-model" if name == "esprit_llm" else None,
            ),
        ):
            _summarize_inherited_context(msgs, "test task")

        # Should pass the old messages (not the recent ones) to summarizer
        call_args = mock_summarize.call_args
        old_msgs_passed = call_args[0][0]
        assert len(old_msgs_passed) == expected_old_count

    def test_fallback_when_summarizer_returns_first_message(self) -> None:
        msgs = _make_messages(20)
        old_msgs = msgs[:-_RECENT_MESSAGES_TO_KEEP]

        with (
            patch(
                "esprit.tools.agents_graph.agents_graph_actions._env_flag",
                return_value=True,
            ),
            patch(
                "esprit.llm.memory_compressor.summarize_messages",
                return_value=old_msgs[0],
            ),
            patch(
                "esprit.tools.agents_graph.agents_graph_actions.Config.get",
                side_effect=lambda name: "test-model" if name == "esprit_llm" else None,
            ),
        ):
            result = _summarize_inherited_context(msgs, "test task")

        # summarize_messages uses this as a failure sentinel; ensure we fall back
        # to brief formatting over old messages instead of keeping only one.
        assert "message 0" in result
        assert "message 1" in result


# ── _run_agent_in_thread context branching ───────────────────────


def _reset_agents_graph_globals() -> None:
    _agent_graph["nodes"].clear()
    _agent_graph["edges"].clear()
    _agent_messages.clear()
    _running_agents.clear()
    _agent_instances.clear()
    _agent_states.clear()


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

    def test_threshold_boundary_uses_short_path(self) -> None:
        """Exactly 15 inherited messages should keep existing behavior."""
        state = MagicMock()
        state.task = "test task"
        state.agent_id = "agent_789"
        state.parent_id = "agent_parent"
        state.agent_name = "Test Agent"

        msgs = _make_messages(15)  # Exactly threshold

        from esprit.tools.agents_graph import agents_graph_actions as mod

        mod._agent_graph["nodes"]["agent_parent"] = {"name": "Parent", "task": "parent task"}
        mod._agent_graph["nodes"]["agent_789"] = {
            "name": "Test Agent",
            "task": "test task",
            "status": "running",
            "parent_id": "agent_parent",
        }

        with patch.object(mod, "_summarize_inherited_context") as mock_summarize:
            agent = MagicMock()

            try:
                mod._run_agent_in_thread(agent, state, msgs)
            except Exception:
                pass

            mock_summarize.assert_not_called()

        # Clean up
        mod._agent_graph["nodes"].pop("agent_789", None)
        mod._agent_graph["nodes"].pop("agent_parent", None)

    def test_short_history_preserves_tool_metadata(self) -> None:
        """Short-history replay must preserve tool_call_id for Anthropic compatibility."""
        from esprit.tools.agents_graph import agents_graph_actions as mod

        state = AgentState(
            agent_id="agent_meta",
            parent_id="agent_parent",
            agent_name="Test Agent",
            task="test task",
        )

        msgs = [
            {
                "role": "assistant",
                "content": "calling tool",
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "browser_action", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "content": "done",
                "tool_call_id": "call_abc",
                "name": "browser_action",
            },
        ]

        class DummyAgent:
            async def agent_loop(self, task: str) -> dict[str, Any]:
                return {"ok": True, "task": task}

        mod._agent_graph["nodes"]["agent_parent"] = {"name": "Parent", "task": "parent task"}
        mod._agent_graph["nodes"]["agent_meta"] = {
            "name": "Test Agent",
            "task": "test task",
            "status": "running",
            "parent_id": "agent_parent",
        }

        try:
            mod._run_agent_in_thread(DummyAgent(), state, msgs)
            tool_msg = next(m for m in state.messages if m.get("role") == "tool")
            assistant_msg = next(
                m for m in state.messages if m.get("role") == "assistant" and m.get("tool_calls")
            )

            assert tool_msg["tool_call_id"] == "call_abc"
            assert tool_msg["name"] == "browser_action"
            assert assistant_msg["tool_calls"][0]["id"] == "call_abc"
        finally:
            mod._agent_graph["nodes"].pop("agent_meta", None)
            mod._agent_graph["nodes"].pop("agent_parent", None)

    def test_short_history_preserves_native_tool_metadata(self) -> None:
        """Short inherited history should keep native tool metadata intact."""
        state = MagicMock()
        state.task = "test task"
        state.agent_id = "agent_meta"
        state.parent_id = "agent_parent"
        state.agent_name = "Metadata Agent"
        state.model_dump.return_value = {"agent_id": "agent_meta"}
        state.stop_requested = False
        state.is_waiting_for_input.return_value = False

        msgs: list[dict[str, Any]] = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_files", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "content": "{'files': []}",
                "tool_call_id": "call_1",
            },
        ]

        from esprit.tools.agents_graph import agents_graph_actions as mod

        mod._agent_graph["nodes"]["agent_parent"] = {"name": "Parent", "task": "parent task"}
        mod._agent_graph["nodes"]["agent_meta"] = {
            "name": "Metadata Agent",
            "task": "test task",
            "status": "running",
            "parent_id": "agent_parent",
        }

        agent = MagicMock()

        async def _return_success(_task: str) -> dict[str, Any]:
            return {"success": True}

        agent.agent_loop.side_effect = _return_success

        mod._run_agent_in_thread(agent, state, msgs)

        assistant_calls = [
            call for call in state.add_message.call_args_list if call.args and call.args[0] == "assistant"
        ]
        assert assistant_calls
        assert assistant_calls[0].kwargs.get("tool_calls")

        tool_calls = [
            call for call in state.add_message.call_args_list if call.args and call.args[0] == "tool"
        ]
        assert tool_calls
        assert tool_calls[0].kwargs.get("tool_call_id") == "call_1"

        mod._agent_graph["nodes"].pop("agent_meta", None)
        mod._agent_graph["nodes"].pop("agent_parent", None)

    def test_short_history_sanitizes_malformed_tool_messages(self) -> None:
        """Malformed tool messages should be downgraded before inheritance replay."""
        state = MagicMock()
        state.task = "test task"
        state.agent_id = "agent_sanitize"
        state.parent_id = "agent_parent"
        state.agent_name = "Sanitize Agent"
        state.model_dump.return_value = {"agent_id": "agent_sanitize"}
        state.stop_requested = False
        state.is_waiting_for_input.return_value = False

        msgs: list[dict[str, Any]] = [
            {"role": "assistant", "content": "reading files"},
            {"role": "tool", "content": "{'files': []}"},
        ]

        from esprit.tools.agents_graph import agents_graph_actions as mod

        mod._agent_graph["nodes"]["agent_parent"] = {"name": "Parent", "task": "parent task"}
        mod._agent_graph["nodes"]["agent_sanitize"] = {
            "name": "Sanitize Agent",
            "task": "test task",
            "status": "running",
            "parent_id": "agent_parent",
        }

        agent = MagicMock()

        async def _return_success(_task: str) -> dict[str, Any]:
            return {"success": True}

        agent.agent_loop.side_effect = _return_success

        mod._run_agent_in_thread(agent, state, msgs)

        malformed_tool_calls = [
            call
            for call in state.add_message.call_args_list
            if call.args and call.args[0] == "tool"
        ]
        assert malformed_tool_calls == []

        user_contents = [
            call.args[1]
            for call in state.add_message.call_args_list
            if call.args and call.args[0] == "user" and len(call.args) > 1
        ]
        assert any("tool metadata was incomplete" in str(content) for content in user_contents)

        mod._agent_graph["nodes"].pop("agent_sanitize", None)
        mod._agent_graph["nodes"].pop("agent_parent", None)


class TestRunAgentFailureStatusPropagation:
    def setup_method(self) -> None:
        _reset_agents_graph_globals()

    def teardown_method(self) -> None:
        _reset_agents_graph_globals()

    def test_subagent_failed_result_marks_failed_and_notifies_parent(self) -> None:
        from esprit.tools.agents_graph import agents_graph_actions as mod

        parent_id = "agent_parent"
        child_id = "agent_child"

        mod._agent_graph["nodes"][parent_id] = {
            "name": "Root Agent",
            "task": "root task",
            "status": "running",
            "parent_id": None,
        }
        mod._agent_graph["nodes"][child_id] = {
            "name": "Child Agent",
            "task": "child task",
            "status": "running",
            "parent_id": parent_id,
            "created_at": "2026-01-01T00:00:00+00:00",
            "finished_at": None,
            "result": None,
        }

        state = MagicMock()
        state.agent_id = child_id
        state.agent_name = "Child Agent"
        state.parent_id = parent_id
        state.task = "child"
        state.model_dump.return_value = {"agent_id": child_id}
        state.add_message = MagicMock()
        state.stop_requested = False

        failing_agent = MagicMock()

        async def _return_failed(_task: str) -> dict[str, Any]:
            return {"success": False, "error": "LLM processing timed out after 360s"}

        failing_agent.agent_loop.side_effect = _return_failed

        mod._run_agent_in_thread(failing_agent, state, [])

        node = mod._agent_graph["nodes"][child_id]
        assert node["status"] == "failed"
        assert node["result"]["success"] is False
        assert "LLM processing timed out after 360s" in node["result"]["summary"]

        parent_messages = mod._agent_messages.get(parent_id, [])
        assert len(parent_messages) == 1
        assert "<agent_completion_report>" in parent_messages[0]["content"]
        assert "<status>FAILED</status>" in parent_messages[0]["content"]
        assert "LLM processing timed out after 360s" in parent_messages[0]["content"]

    def test_subagent_finished_status_counts_as_completed_for_finish_scan_guard(self) -> None:
        from esprit.tools.agents_graph import agents_graph_actions as graph_mod
        from esprit.tools.finish.finish_actions import _check_active_agents

        root_id = "agent_root"
        child_id = "agent_child_finished"

        graph_mod._agent_graph["nodes"][root_id] = {
            "name": "Root Agent",
            "task": "root task",
            "status": "running",
            "parent_id": None,
        }
        graph_mod._agent_graph["nodes"][child_id] = {
            "name": "Child Agent",
            "task": "child task",
            "status": "finished",
            "parent_id": root_id,
            "created_at": "2026-01-01T00:00:00+00:00",
            "finished_at": "2026-01-01T00:05:00+00:00",
            "result": {"success": True},
        }

        root_state = MagicMock()
        root_state.agent_id = root_id

        active = _check_active_agents(root_state)

        assert active is None

    def test_subagent_waiting_status_blocks_finish_scan_guard(self) -> None:
        from esprit.tools.agents_graph import agents_graph_actions as graph_mod
        from esprit.tools.finish.finish_actions import _check_active_agents

        root_id = "agent_root"
        child_id = "agent_child_waiting"

        graph_mod._agent_graph["nodes"][root_id] = {
            "name": "Root Agent",
            "task": "root task",
            "status": "running",
            "parent_id": None,
        }
        graph_mod._agent_graph["nodes"][child_id] = {
            "name": "Child Agent",
            "task": "child task",
            "status": "waiting",
            "parent_id": root_id,
            "created_at": "2026-01-01T00:00:00+00:00",
            "finished_at": None,
            "result": None,
        }

        root_state = MagicMock()
        root_state.agent_id = root_id

        active = _check_active_agents(root_state)

        assert active is not None
        assert active["error"] == "agents_still_active"
        assert active["total_active"] == 1
        assert active["active_agents"][0]["id"] == child_id
        assert active["active_agents"][0]["status"] == "waiting"

    def test_subagent_queued_status_blocks_finish_scan_guard(self) -> None:
        from esprit.tools.agents_graph import agents_graph_actions as graph_mod
        from esprit.tools.finish.finish_actions import _check_active_agents

        root_id = "agent_root"
        child_id = "agent_child_queued"

        graph_mod._agent_graph["nodes"][root_id] = {
            "name": "Root Agent",
            "task": "root task",
            "status": "running",
            "parent_id": None,
        }
        graph_mod._agent_graph["nodes"][child_id] = {
            "name": "Child Agent",
            "task": "child task",
            "status": "queued",
            "parent_id": root_id,
            "created_at": "2026-01-01T00:00:00+00:00",
            "finished_at": None,
            "result": None,
        }

        root_state = MagicMock()
        root_state.agent_id = root_id

        active = _check_active_agents(root_state)

        assert active is not None
        assert active["error"] == "agents_still_active"
        assert active["total_active"] == 1
        assert active["active_agents"][0]["id"] == child_id
        assert active["active_agents"][0]["status"] == "queued"

    def test_waiting_failed_subagent_result_is_marked_failed(self) -> None:
        from esprit.tools.agents_graph import agents_graph_actions as mod

        parent_id = "agent_parent"
        child_id = "agent_child_waiting_failed"

        mod._agent_graph["nodes"][parent_id] = {
            "name": "Root Agent",
            "task": "root task",
            "status": "running",
            "parent_id": None,
        }
        mod._agent_graph["nodes"][child_id] = {
            "name": "Child Agent",
            "task": "child task",
            "status": "waiting",
            "parent_id": parent_id,
            "created_at": "2026-01-01T00:00:00+00:00",
            "finished_at": None,
            "result": None,
        }

        state = MagicMock()
        state.agent_id = child_id
        state.agent_name = "Child Agent"
        state.parent_id = parent_id
        state.task = "child"
        state.model_dump.return_value = {"agent_id": child_id}
        state.add_message = MagicMock()
        state.stop_requested = False
        state.is_waiting_for_input.return_value = True

        failing_agent = MagicMock()

        async def _return_failed(_task: str) -> dict[str, Any]:
            return {"success": False, "error": "LLM processing timed out after 360s"}

        failing_agent.agent_loop.side_effect = _return_failed

        mod._run_agent_in_thread(failing_agent, state, [])

        node = mod._agent_graph["nodes"][child_id]
        assert node["status"] == "failed"
        assert node["result"]["success"] is False
