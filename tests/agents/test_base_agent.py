"""Tests for BaseAgent native tool-call payload construction."""

from datetime import UTC, datetime, timedelta

from esprit.agents.base_agent import BaseAgent
from esprit.agents.state import AgentState
from esprit.llm import LLMRequestFailedError


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

    def test_llm_failed_root_resumes_from_subagent_message(self) -> None:
        state = AgentState(parent_id=None)
        state.enter_waiting_state(llm_failed=True)

        assert BaseAgent._should_resume_waiting_on_message(state, "agent_child")

    def test_llm_failed_ignores_unrelated_agent_message(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=True)

        assert not BaseAgent._should_resume_waiting_on_message(state, "agent_sibling")

    def test_llm_failed_root_ignores_missing_sender(self) -> None:
        state = AgentState(parent_id=None)
        state.enter_waiting_state(llm_failed=True)

        assert not BaseAgent._should_resume_waiting_on_message(state, None)

    def test_normal_waiting_resumes_from_any_sender(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=False)

        assert BaseAgent._should_resume_waiting_on_message(state, "agent_sibling")

    def test_llm_failed_does_not_auto_resume_on_waiting_timeout(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=True)
        state.waiting_start_time = datetime.now(UTC) - timedelta(hours=2)

        assert state.has_waiting_timeout() is False

    def test_waiting_timeout_uses_custom_seconds(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=False)
        state.waiting_start_time = datetime.now(UTC) - timedelta(seconds=90)

        assert state.has_waiting_timeout(timeout_seconds=60.0) is True
        assert state.has_waiting_timeout(timeout_seconds=120.0) is False


class TestLLMAutoResumePolicy:
    @staticmethod
    def _make_agent_for_waiting_checks(state: AgentState) -> BaseAgent:
        agent = BaseAgent.__new__(BaseAgent)
        agent.state = state
        agent._last_llm_failure_retryable = True
        agent._llm_auto_resume_attempts = 0
        agent._max_llm_auto_resume_attempts = 2
        agent._llm_auto_resume_cooldown = 10.0
        return agent

    def test_retryable_status_code_classification(self) -> None:
        assert BaseAgent._is_retryable_llm_status_code(None) is True
        assert BaseAgent._is_retryable_llm_status_code(429) is True
        assert BaseAgent._is_retryable_llm_status_code(503) is True
        assert BaseAgent._is_retryable_llm_status_code(400) is False
        assert BaseAgent._is_retryable_llm_status_code(401) is False

    def test_extracts_status_code_from_error_details(self) -> None:
        error = LLMRequestFailedError(
            "failed",
            details="AnthropicException: HTTP 429 Rate limit exceeded",
            status_code=None,
        )

        assert BaseAgent._extract_status_code_from_llm_error(error) == 429

    def test_subagent_can_auto_resume_retryable_llm_failure(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=True)
        state.waiting_start_time = datetime.now(UTC) - timedelta(seconds=20)
        agent = self._make_agent_for_waiting_checks(state)

        assert agent._should_auto_resume_llm_failure() is True

    def test_root_agent_does_not_auto_resume_llm_failure(self) -> None:
        state = AgentState(parent_id=None)
        state.enter_waiting_state(llm_failed=True)
        state.waiting_start_time = datetime.now(UTC) - timedelta(seconds=20)
        agent = self._make_agent_for_waiting_checks(state)

        assert agent._should_auto_resume_llm_failure() is False

    def test_root_agent_can_auto_resume_when_enabled(self) -> None:
        state = AgentState(parent_id=None)
        state.enter_waiting_state(llm_failed=True)
        state.waiting_start_time = datetime.now(UTC) - timedelta(seconds=20)
        agent = self._make_agent_for_waiting_checks(state)
        agent._allow_root_llm_auto_resume = True

        assert agent._should_auto_resume_llm_failure() is True

    def test_auto_resume_respects_attempt_cap(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=True)
        state.waiting_start_time = datetime.now(UTC) - timedelta(seconds=20)
        agent = self._make_agent_for_waiting_checks(state)
        agent._llm_auto_resume_attempts = 2

        assert agent._should_auto_resume_llm_failure() is False
