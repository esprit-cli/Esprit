"""Tests for BaseAgent native tool-call payload construction."""

from esprit.agents.base_agent import BaseAgent
from esprit.agents.state import AgentState


class TestBuildNativeToolCalls:
    def test_returns_payload_when_all_invocations_have_ids(self) -> None:
        actions = [
            {"toolName": "first_tool", "args": {"a": 1}, "tool_call_id": "call_1"},
            {"toolName": "second_tool", "args": {"b": "x"}, "tool_call_id": "call_2"},
        ]

        payload = BaseAgent._build_native_tool_calls(actions)

        assert payload is not None
        assert len(payload) == 2
        assert payload[0]["id"] == "call_1"
        assert payload[0]["function"]["name"] == "first_tool"
        assert payload[1]["id"] == "call_2"
        assert payload[1]["function"]["name"] == "second_tool"

    def test_returns_none_when_tool_call_ids_are_mixed(self) -> None:
        actions = [
            {"toolName": "first_tool", "args": {}, "tool_call_id": "call_1"},
            {"toolName": "second_tool", "args": {}},
        ]

        assert BaseAgent._build_native_tool_calls(actions) is None

    def test_returns_none_when_all_tool_call_ids_missing(self) -> None:
        actions = [
            {"toolName": "first_tool", "args": {}},
            {"toolName": "second_tool", "args": {}},
        ]

        assert BaseAgent._build_native_tool_calls(actions) is None


class TestWaitingResumePolicy:
    def test_llm_failed_resumes_from_user_message(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=True)

        assert BaseAgent._should_resume_waiting_on_message(state, "user")

    def test_llm_failed_resumes_from_parent_message(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=True)

        assert BaseAgent._should_resume_waiting_on_message(state, "agent_parent")

    def test_llm_failed_ignores_unrelated_agent_message(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=True)

        assert not BaseAgent._should_resume_waiting_on_message(state, "agent_sibling")

    def test_normal_waiting_resumes_from_any_sender(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=False)

        assert BaseAgent._should_resume_waiting_on_message(state, "agent_sibling")
