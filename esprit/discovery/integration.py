"""Agent loop integration for the discovery engine.

Provides hook functions that can be called from BaseAgent._process_iteration
and _execute_actions to wire discovery into the agent loop without modifying
the core agent flow.
"""

from __future__ import annotations

import logging
from typing import Any

from .hypothesis_generator import HypothesisGenerator
from .models import DiscoveryState, EvidenceRef
from .prioritizer import HypothesisPrioritizer
from .scheduler import ExperimentScheduler
from .signal_extractor import SignalExtractor
from .tracker import DiscoveryTracker


logger = logging.getLogger(__name__)


class DiscoveryIntegration:
    """Composable discovery engine integration for the agent loop.

    Usage:
        # Initialize once per root agent
        discovery = DiscoveryIntegration()

        # Before LLM call — inject hypothesis context
        context_block = discovery.build_context_block()

        # After tool results — process signals
        discovery.process_tool_result(tool_name, tool_args, result)

        # Before finishing — check for untested hypotheses
        has_work = discovery.has_untested_high_priority()
    """

    def __init__(
        self,
        state: DiscoveryState | None = None,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._state = state or DiscoveryState()
        self._tracker = DiscoveryTracker(self._state)
        self._signal_extractor = SignalExtractor()
        self._hypothesis_generator = HypothesisGenerator(self._state)
        self._prioritizer = HypothesisPrioritizer(self._state)
        self._scheduler = ExperimentScheduler(self._state)

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def state(self) -> DiscoveryState:
        return self._state

    @property
    def tracker(self) -> DiscoveryTracker:
        return self._tracker

    @property
    def scheduler(self) -> ExperimentScheduler:
        return self._scheduler

    # ── Pre-LLM hook ──────────────────────────────────────────────────

    def build_context_block(self, max_hypotheses: int = 5) -> str | None:
        """Build a structured context block of top hypotheses for LLM injection.

        Returns None if discovery is disabled or there are no queued hypotheses.
        """
        if not self._enabled:
            return None

        queued = self._prioritizer.rank_queued(limit=max_hypotheses)
        if not queued:
            return None

        metrics = self._state.update_metrics()

        lines = [
            "<discovery_context>",
            f"  <metrics total_hypotheses='{metrics.total_hypotheses}' "
            f"validated='{metrics.validated_hypotheses}' "
            f"queued='{metrics.queued_hypotheses}' "
            f"running_experiments='{self._state.get_running_experiments_count()}' />",
            "  <queued_hypotheses>",
        ]

        for h in queued:
            lines.append(
                f"    <hypothesis id='{h.id}' priority='{h.priority:.3f}' "
                f"class='{h.vulnerability_class}'>"
                f"{h.title} → {h.target}"
                f"</hypothesis>"
            )

        lines.append("  </queued_hypotheses>")
        lines.append("</discovery_context>")

        return "\n".join(lines)

    # ── Post-tool hook ────────────────────────────────────────────────

    def process_tool_result(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        result: Any,
    ) -> int:
        """Process a tool execution result for anomaly signals.

        Returns the number of new hypotheses generated.
        """
        if not self._enabled:
            return 0

        anomalies = self._signal_extractor.extract_from_tool_result(
            tool_name, tool_args, result
        )
        if not anomalies:
            return 0

        # Register anomalies
        for anomaly in anomalies:
            self._state.add_anomaly(anomaly)

        # Generate hypotheses from anomalies
        new_hypotheses = self._hypothesis_generator.generate_from_anomalies(anomalies)

        # Deduplicate
        accepted = self._prioritizer.deduplicate_new_hypotheses(new_hypotheses)

        # Add accepted hypotheses to state
        for h in accepted:
            self._state.add_hypothesis(h)

        if accepted:
            logger.info(
                f"Discovery: {len(anomalies)} anomalies → "
                f"{len(new_hypotheses)} hypotheses → "
                f"{len(accepted)} accepted"
            )

        return len(accepted)

    # ── Pre-finish hook ───────────────────────────────────────────────

    def has_untested_high_priority(self, threshold: float = 0.5) -> bool:
        """Check if there are high-priority untested hypotheses.

        Used before finish_scan to warn about unexplored attack surface.
        """
        if not self._enabled:
            return False

        queued = self._prioritizer.rank_queued(limit=1)
        if not queued:
            return False

        return queued[0].priority >= threshold

    def get_untested_summary(self, threshold: float = 0.3) -> str:
        """Get a human-readable summary of untested hypotheses above threshold."""
        queued = self._prioritizer.rank_queued()
        above = [h for h in queued if h.priority >= threshold]

        if not above:
            return "No untested hypotheses above threshold."

        lines = [f"{len(above)} untested hypotheses remain:"]
        for h in above[:5]:
            lines.append(
                f"  - [{h.priority:.2f}] {h.vulnerability_class}: {h.title}"
            )
        if len(above) > 5:
            lines.append(f"  ... and {len(above) - 5} more")
        return "\n".join(lines)

    # ── Experiment lifecycle ──────────────────────────────────────────

    def complete_experiment_from_agent_result(
        self,
        agent_id: str,
        success: bool,
        result_summary: str,
        findings: list[str] | None = None,
    ) -> None:
        """Process a subagent completion and update experiment status.

        Called when a discovery subagent finishes (via agent_finish).
        """
        if not self._enabled:
            return

        # Find the experiment for this agent
        for experiment in self._state.experiments:
            if experiment.agent_id == agent_id:
                if success and findings:
                    evidence = [
                        EvidenceRef(
                            source="subagent",
                            ref_id=agent_id,
                            description=f"Finding: {f[:100]}",
                        )
                        for f in findings[:5]
                    ]
                    self._tracker.complete_experiment(
                        experiment.id, "validated", evidence
                    )
                elif success:
                    self._tracker.complete_experiment(experiment.id, "inconclusive")
                else:
                    self._tracker.complete_experiment(experiment.id, "falsified")
                return

    # ── Persistence ───────────────────────────────────────────────────

    def get_persistence_data(self) -> dict[str, Any]:
        """Get serializable discovery data for tracer persistence."""
        self._state.update_metrics()
        return self._state.to_persistence_dict()
