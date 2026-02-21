"""Tests for BaseAgent native tool-call payload construction and watchdog behavior."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from esprit.agents.base_agent import BaseAgent
from esprit.agents.state import AgentState
from esprit.llm import LLMRequestFailedError
from esprit.telemetry.tracer import Tracer


class _DummyAgent(BaseAgent):
    agent_name = "DummyAgent"


def _make_agent(**overrides: object) -> _DummyAgent:
    cfg = {
        "llm_config": SimpleNamespace(model_name="anthropic/claude-3-5-sonnet-20241022"),
        "state": AgentState(agent_name="DummyAgent", task=""),
        "non_interactive": True,
        "max_iterations": 1,
    }
    cfg.update(overrides)

    with patch("esprit.agents.base_agent.LLM") as mock_llm:
        mock_llm.return_value.set_agent_identity.return_value = None
        return _DummyAgent(cfg)


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

    def test_auto_resume_respects_attempt_cap(self) -> None:
        state = AgentState(parent_id="agent_parent")
        state.enter_waiting_state(llm_failed=True)
        state.waiting_start_time = datetime.now(UTC) - timedelta(seconds=20)
        agent = self._make_agent_for_waiting_checks(state)
        agent._llm_auto_resume_attempts = 2

        assert agent._should_auto_resume_llm_failure() is False

class TestWatchdogConfigParsing:
    def test_llm_watchdog_defaults_are_capped_when_llm_timeout_is_high(self) -> None:
        agent = _make_agent(
            llm_config=SimpleNamespace(
                model_name="anthropic/claude-3-5-sonnet-20241022",
                timeout=1800,
            )
        )

        assert agent.llm_watchdog_timeout_s == 600

    def test_watchdog_values_are_clamped_and_policy_is_validated(self) -> None:
        agent = _make_agent(
            stall_policy="invalid",
            llm_watchdog_timeout_s=0,
            tool_watchdog_timeout_s=-5,
            stall_grace_period_s=-1,
            max_stall_recoveries=-2,
        )

        assert agent.stall_policy == "auto_recover"
        assert agent.llm_watchdog_timeout_s == 1
        assert agent.tool_watchdog_timeout_s == 1
        assert agent.stall_grace_period_s == 1
        assert agent.max_stall_recoveries == 0

    def test_llm_wait_progress_touches_agent_heartbeat(self) -> None:
        agent = _make_agent()
        tracer = Mock(spec=Tracer)

        with patch("esprit.telemetry.tracer.get_global_tracer", return_value=tracer):
            agent._handle_llm_wait_progress(
                "before_llm_processing",
                "queued for global LLM slot (3s)",
            )

        assert agent.state.heartbeat_phase == "before_llm_processing"
        assert agent.state.heartbeat_detail == "queued for global LLM slot (3s)"
        tracer.touch_agent_heartbeat.assert_called_once()


class TestBaseAgentWatchdogAndRecovery:
    def test_execute_actions_timeout_raises_runtime_error(self) -> None:
        agent = _make_agent(tool_watchdog_timeout_s=1)

        async def _slow_tools(*_args: object, **_kwargs: object) -> bool:
            await asyncio.sleep(2)
            return False

        with (
            patch("esprit.agents.base_agent.process_tool_invocations", side_effect=_slow_tools),
            pytest.raises(LLMRequestFailedError, match="Tool execution timed out after 1s"),
        ):
            asyncio.run(agent._execute_actions([{"toolName": "noop", "args": {}}], tracer=None))

    def test_execute_actions_does_not_cancel_long_running_tools_during_heartbeat_poll(self) -> None:
        agent = _make_agent(tool_watchdog_timeout_s=30)
        cancelled = {"value": False}

        async def _slow_tools(*_args: object, **_kwargs: object) -> bool:
            try:
                await asyncio.sleep(5.2)
            except asyncio.CancelledError:
                cancelled["value"] = True
                raise
            return False

        with patch("esprit.agents.base_agent.process_tool_invocations", side_effect=_slow_tools):
            should_finish = asyncio.run(
                agent._execute_actions([{"toolName": "noop", "args": {}}], tracer=None)
            )

        assert should_finish is False
        assert cancelled["value"] is False

    def test_llm_timeout_routes_to_llm_error_handling(self) -> None:
        agent = _make_agent(llm_watchdog_timeout_s=1)
        agent.state.task = "run"
        agent._initialize_sandbox_and_state = AsyncMock(return_value=None)
        agent._check_agent_messages = Mock()
        agent._wait_for_input = AsyncMock(return_value=None)
        agent._handle_iteration_error = AsyncMock(return_value=True)

        async def _slow_iteration(_tracer: object) -> bool:
            await asyncio.sleep(2)
            return False

        observed: dict[str, str] = {}

        def _capture_llm_error(err: LLMRequestFailedError, _tracer: object) -> dict[str, object]:
            observed["msg"] = str(err)
            return {"success": False, "error": str(err)}

        agent._process_iteration = _slow_iteration
        agent._handle_llm_error = _capture_llm_error

        result = asyncio.run(agent.agent_loop("task"))

        assert result["success"] is False
        assert "LLM processing timed out after 1s" in observed["msg"]

    def test_auto_recover_sets_waiting_when_max_recoveries_exceeded(self) -> None:
        agent = _make_agent(stall_grace_period_s=1, max_stall_recoveries=1)
        stale = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        agent.state.last_heartbeat_at = stale
        agent.state.stall_count = 1

        tracer = Mock(spec=Tracer)

        async def _run() -> None:
            agent._current_task = asyncio.current_task()
            recovered = agent._maybe_auto_recover_stall(tracer)
            assert recovered is False

        asyncio.run(_run())

        assert agent.state.llm_failed is True
        assert agent.state.waiting_for_input is True
        tracer.update_agent_status.assert_called_once()

    def test_auto_recover_records_recovery_when_stale(self) -> None:
        agent = _make_agent(stall_grace_period_s=1, max_stall_recoveries=3)
        stale = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        agent.state.last_heartbeat_at = stale

        tracer = Mock(spec=Tracer)

        async def _run() -> None:
            agent._current_task = asyncio.current_task()
            recovered = agent._maybe_auto_recover_stall(tracer)

            assert recovered is True
            assert agent.state.stall_count == 1
            assert agent.state.last_recovery_reason is not None

        asyncio.run(_run())

        tracer.update_agent_status.assert_any_call(agent.state.agent_id, "stalled_recovered")
        tracer.update_agent_status.assert_any_call(agent.state.agent_id, "running")

    def test_auto_recover_skips_when_waiting_for_input(self) -> None:
        agent = _make_agent(stall_grace_period_s=1, max_stall_recoveries=3)
        stale = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        agent.state.last_heartbeat_at = stale
        agent.state.enter_waiting_state()

        tracer = Mock(spec=Tracer)

        async def _run() -> None:
            agent._current_task = asyncio.current_task()
            recovered = agent._maybe_auto_recover_stall(tracer)
            assert recovered is False

        asyncio.run(_run())

        assert agent.state.stall_count == 0
        tracer.update_agent_status.assert_not_called()

    def test_auto_recover_skips_without_active_task(self) -> None:
        agent = _make_agent(stall_grace_period_s=1, max_stall_recoveries=3)
        stale = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        agent.state.last_heartbeat_at = stale
        agent._current_task = None

        tracer = Mock(spec=Tracer)

        recovered = agent._maybe_auto_recover_stall(tracer)

        assert recovered is False
        assert agent.state.stall_count == 0
        tracer.update_agent_status.assert_not_called()


class TestBaseAgentResumedStatus:
    def test_resumed_waiting_agent_sets_waiting_status_in_tracer(self) -> None:
        state = AgentState(agent_name="DummyAgent", task="", parent_id=None)
        state.enter_waiting_state()

        tracer = Mock(spec=Tracer)
        tracer.agents = {state.agent_id: {"status": "waiting"}}

        with (
            patch("esprit.agents.base_agent.LLM") as mock_llm,
            patch("esprit.telemetry.tracer.get_global_tracer", return_value=tracer),
        ):
            mock_llm.return_value.set_agent_identity.return_value = None
            _DummyAgent(
                {
                    "llm_config": SimpleNamespace(model_name="anthropic/claude-3-5-sonnet-20241022"),
                    "state": state,
                    "non_interactive": True,
                    "max_iterations": 1,
                }
            )

        tracer.update_agent_status.assert_any_call(state.agent_id, "resumed")
        tracer.update_agent_status.assert_any_call(state.agent_id, "waiting")


class TestNonInteractiveLlmRecovery:
    def test_non_interactive_recovers_from_timeout_errors(self) -> None:
        agent = _make_agent(non_interactive=True, max_stall_recoveries=2)
        tracer = Mock(spec=Tracer)
        tracer.log_tool_execution_start.return_value = 123

        err = LLMRequestFailedError(
            "LLM processing timed out after 600s",
            details="TimeoutError",
        )
        result = agent._handle_llm_error(err, tracer)

        assert result is None
        assert agent.state.stall_count == 1
        tracer.update_agent_status.assert_any_call(agent.state.agent_id, "stalled_recovered")
        tracer.update_agent_status.assert_any_call(agent.state.agent_id, "running")

    def test_non_interactive_fails_after_recovery_budget_exhausted(self) -> None:
        agent = _make_agent(non_interactive=True, max_stall_recoveries=1)
        agent.state.stall_count = 1
        tracer = Mock(spec=Tracer)
        tracer.log_tool_execution_start.return_value = 456

        err = LLMRequestFailedError(
            "LLM processing timed out after 600s",
            details="TimeoutError",
        )
        result = agent._handle_llm_error(err, tracer)

        assert result is not None
        assert result["success"] is False
        assert agent.state.completed is True

    def test_non_interactive_does_not_retry_non_recoverable_llm_error(self) -> None:
        agent = _make_agent(non_interactive=True, max_stall_recoveries=3)
        tracer = Mock(spec=Tracer)
        tracer.log_tool_execution_start.return_value = 789

        err = LLMRequestFailedError(
            "OpenAI credentials are not authorized for this Codex model",
            details="missing api.responses.write scope",
        )
        result = agent._handle_llm_error(err, tracer)

        assert result is not None
        assert result["success"] is False
        assert agent.state.stall_count == 0
