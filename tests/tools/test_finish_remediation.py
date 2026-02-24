"""Tests for the remediation completeness gate in finish_actions.py.

Covers:
- P1: Coverage check — fixing agent count must match vulnerability count
- P1: Per-scan bounce counter — scoped by agent_id, not global
- P2: Error message uses valid skills format (comma-separated, no brackets)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from esprit.tools.finish import finish_actions
from esprit.tools.finish.finish_actions import _check_remediation_completeness


class _FakeAgentState:
    """Minimal agent state stub."""

    def __init__(self, agent_id: str = "root_1", is_whitebox: bool = True) -> None:
        self.agent_id = agent_id
        self.is_whitebox = is_whitebox
        self.parent_id = None


def _make_tracer(vuln_count: int) -> MagicMock:
    tracer = MagicMock()
    tracer.vulnerability_reports = [MagicMock() for _ in range(vuln_count)]
    return tracer


@pytest.fixture(autouse=True)
def _reset_state() -> Any:
    """Clear per-scan bounce counters and agent graph between tests."""
    finish_actions._remediation_bounce_counts.clear()
    # Save and restore the agent graph
    from esprit.tools.agents_graph import agents_graph_actions

    old_nodes = agents_graph_actions._agent_graph["nodes"].copy()
    old_edges = agents_graph_actions._agent_graph["edges"][:]
    agents_graph_actions._agent_graph["nodes"] = {}
    agents_graph_actions._agent_graph["edges"] = []
    yield
    agents_graph_actions._agent_graph["nodes"] = old_nodes
    agents_graph_actions._agent_graph["edges"] = old_edges


def _add_fixing_agent(name: str, status: str = "finished") -> None:
    """Add a fixing agent node to the global agent graph."""
    from esprit.tools.agents_graph import agents_graph_actions

    agent_id = f"fix_{len(agents_graph_actions._agent_graph['nodes'])}"
    agents_graph_actions._agent_graph["nodes"][agent_id] = {
        "name": name,
        "status": status,
        "task": "fix something",
    }


# ── Non-whitebox scans should bypass the gate ──


class TestRemediationGateBypass:
    def test_non_whitebox_bypasses(self) -> None:
        state = _FakeAgentState(is_whitebox=False)
        assert _check_remediation_completeness(state) is None

    def test_no_agent_state_bypasses(self) -> None:
        assert _check_remediation_completeness(None) is None


# ── Coverage checks ──


class TestRemediationCoverage:
    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_zero_vulns_passes(self, mock_tracer_fn: MagicMock) -> None:
        """No vulnerabilities reported -> gate passes."""
        mock_tracer_fn.return_value = _make_tracer(0)
        state = _FakeAgentState()
        assert _check_remediation_completeness(state) is None

    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_full_coverage_passes(self, mock_tracer_fn: MagicMock) -> None:
        """2 vulns, 2 finished fixers -> gate passes."""
        mock_tracer_fn.return_value = _make_tracer(2)
        _add_fixing_agent("SQLi Fixing Agent")
        _add_fixing_agent("XSS Fixing Agent")
        state = _FakeAgentState()
        assert _check_remediation_completeness(state) is None

    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_overcoverage_passes(self, mock_tracer_fn: MagicMock) -> None:
        """1 vuln, 2 finished fixers -> gate passes."""
        mock_tracer_fn.return_value = _make_tracer(1)
        _add_fixing_agent("Fix Agent A")
        _add_fixing_agent("Fix Agent B")
        state = _FakeAgentState()
        assert _check_remediation_completeness(state) is None

    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_partial_coverage_bounces(self, mock_tracer_fn: MagicMock) -> None:
        """3 vulns, 1 finished fixer -> gate bounces with coverage details."""
        mock_tracer_fn.return_value = _make_tracer(3)
        _add_fixing_agent("SQLi Fixing Agent")
        state = _FakeAgentState()
        result = _check_remediation_completeness(state)
        assert result is not None
        assert result["success"] is False
        assert result["error"] == "remediation_incomplete"
        assert result["fixing_agents_completed"] == 1
        assert result["vulnerabilities_reported"] == 3
        assert result["vulnerabilities_without_fixes"] == 2

    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_zero_fixers_bounces(self, mock_tracer_fn: MagicMock) -> None:
        """2 vulns, 0 fixers -> gate bounces."""
        mock_tracer_fn.return_value = _make_tracer(2)
        state = _FakeAgentState()
        result = _check_remediation_completeness(state)
        assert result is not None
        assert result["fixing_agents_completed"] == 0
        assert result["vulnerabilities_without_fixes"] == 2

    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_non_finished_fixer_not_counted(
        self, mock_tracer_fn: MagicMock
    ) -> None:
        """A running fixer should not count toward coverage."""
        mock_tracer_fn.return_value = _make_tracer(1)
        _add_fixing_agent("Fixer", status="running")
        state = _FakeAgentState()
        result = _check_remediation_completeness(state)
        assert result is not None
        assert result["fixing_agents_completed"] == 0

    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_partial_coverage_message_mentions_counts(
        self, mock_tracer_fn: MagicMock
    ) -> None:
        """Partial coverage error message should mention both counts."""
        mock_tracer_fn.return_value = _make_tracer(4)
        _add_fixing_agent("Fix A")
        _add_fixing_agent("Fix B")
        state = _FakeAgentState()
        result = _check_remediation_completeness(state)
        assert result is not None
        assert "4" in result["message"]  # vuln count
        assert "2" in result["message"]  # fixer count


# ── Per-scan bounce counter ──


class TestBounceCounter:
    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_bounces_allow_after_limit(self, mock_tracer_fn: MagicMock) -> None:
        """After MAX bounces, gate allows through."""
        mock_tracer_fn.return_value = _make_tracer(2)
        state = _FakeAgentState(agent_id="scan_A")

        for i in range(finish_actions._MAX_REMEDIATION_BOUNCES):
            result = _check_remediation_completeness(state)
            assert result is not None, f"Bounce {i + 1} should block"

        # Next call should pass
        result = _check_remediation_completeness(state)
        assert result is None

    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_separate_scans_have_independent_counters(
        self, mock_tracer_fn: MagicMock
    ) -> None:
        """Bouncing scan A should not affect scan B's counter."""
        mock_tracer_fn.return_value = _make_tracer(1)

        state_a = _FakeAgentState(agent_id="scan_A")
        state_b = _FakeAgentState(agent_id="scan_B")

        # Bounce scan A twice (hit the limit)
        _check_remediation_completeness(state_a)
        _check_remediation_completeness(state_a)

        # Scan B should still be at bounce 0 -> blocks on first call
        result_b = _check_remediation_completeness(state_b)
        assert result_b is not None
        assert result_b["success"] is False

        # Scan A hits limit on 3rd call -> passes
        result_a = _check_remediation_completeness(state_a)
        assert result_a is None

        # Scan B still on bounce 2 -> blocks
        result_b2 = _check_remediation_completeness(state_b)
        assert result_b2 is not None

    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_counter_not_shared_across_agent_ids(
        self, mock_tracer_fn: MagicMock
    ) -> None:
        """Each agent_id gets its own counter starting at 0."""
        mock_tracer_fn.return_value = _make_tracer(1)

        # Exhaust bounces for scan_X
        state_x = _FakeAgentState(agent_id="scan_X")
        for _ in range(finish_actions._MAX_REMEDIATION_BOUNCES + 1):
            _check_remediation_completeness(state_x)

        # scan_Y should still block
        state_y = _FakeAgentState(agent_id="scan_Y")
        result = _check_remediation_completeness(state_y)
        assert result is not None


# ── Skills format in error messages ──


class TestSkillsFormat:
    @patch("esprit.telemetry.tracer.get_global_tracer")
    def test_error_message_uses_comma_format(
        self, mock_tracer_fn: MagicMock
    ) -> None:
        """Error message should use skills=\"x,y\" not skills: [x, y]."""
        mock_tracer_fn.return_value = _make_tracer(1)
        state = _FakeAgentState()
        result = _check_remediation_completeness(state)
        assert result is not None

        # Check the message and suggestions don't use bracket format
        msg = result["message"]
        assert "[remediation," not in msg
        assert "skills: [" not in msg

        for suggestion in result["suggestions"]:
            assert "[remediation," not in suggestion
            assert "skills: [" not in suggestion

    def test_system_prompt_skills_format(self) -> None:
        """system_prompt.jinja should not use bracket format for skills."""
        import pathlib

        prompt_path = pathlib.Path(__file__).resolve().parents[2] / (
            "esprit/agents/EspritAgent/system_prompt.jinja"
        )
        content = prompt_path.read_text()

        # Should not contain the invalid bracket format
        assert "skills: [remediation," not in content
        assert "skills [remediation," not in content

        # Should contain the valid comma-separated format
        assert 'skills="remediation,' in content
