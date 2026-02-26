"""Discovery state lifecycle tracker."""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    DiscoveryState,
    Experiment,
    ExperimentStatus,
    EvidenceRef,
    Hypothesis,
    HypothesisStatus,
)


logger = logging.getLogger(__name__)


class DiscoveryTracker:
    """Manages hypothesis and experiment lifecycle transitions."""

    def __init__(self, state: DiscoveryState | None = None) -> None:
        self.state = state or DiscoveryState()

    def submit_hypothesis(self, hypothesis: Hypothesis) -> str:
        """Add a new hypothesis and compute its priority."""
        hypothesis_id = self.state.add_hypothesis(hypothesis)
        logger.info(f"Hypothesis submitted: {hypothesis_id} - {hypothesis.title}")
        return hypothesis_id

    def start_experiment(
        self, hypothesis_id: str, agent_id: str, task_description: str
    ) -> str | None:
        """Create and start an experiment for a hypothesis."""
        hypothesis = self.state.get_hypothesis_by_id(hypothesis_id)
        if hypothesis is None:
            logger.warning(f"Hypothesis {hypothesis_id} not found")
            return None

        if hypothesis.status != HypothesisStatus.queued:
            logger.warning(
                f"Hypothesis {hypothesis_id} is {hypothesis.status}, cannot start experiment"
            )
            return None

        running_count = self.state.get_running_experiments_count()
        if running_count >= self.state.max_concurrent_experiments:
            logger.warning(
                f"Max concurrent experiments ({self.state.max_concurrent_experiments}) reached"
            )
            return None

        experiment = Experiment(
            hypothesis_id=hypothesis_id,
            task_description=task_description,
        )
        experiment.start(agent_id)
        experiment_id = self.state.add_experiment(experiment)

        hypothesis.status = HypothesisStatus.in_progress
        hypothesis.experiment_id = experiment_id

        logger.info(
            f"Experiment {experiment_id} started for hypothesis {hypothesis_id} "
            f"by agent {agent_id}"
        )
        return experiment_id

    def complete_experiment(
        self,
        experiment_id: str,
        result: str,
        evidence: list[EvidenceRef] | None = None,
    ) -> None:
        """Mark an experiment as completed and update hypothesis status."""
        experiment = self.state.get_experiment_by_id(experiment_id)
        if experiment is None:
            logger.warning(f"Experiment {experiment_id} not found")
            return

        experiment.complete(result, evidence)

        hypothesis = self.state.get_hypothesis_by_id(experiment.hypothesis_id)
        if hypothesis is None:
            return

        result_lower = result.lower().strip()
        if result_lower == "validated":
            hypothesis.status = HypothesisStatus.validated
        elif result_lower == "falsified":
            hypothesis.status = HypothesisStatus.falsified
        else:
            hypothesis.status = HypothesisStatus.inconclusive

        hypothesis.result_summary = result
        logger.info(
            f"Experiment {experiment_id} completed: {result} "
            f"(hypothesis {experiment.hypothesis_id} -> {hypothesis.status})"
        )

    def fail_experiment(self, experiment_id: str, error: str) -> None:
        """Mark an experiment as failed."""
        experiment = self.state.get_experiment_by_id(experiment_id)
        if experiment is None:
            logger.warning(f"Experiment {experiment_id} not found")
            return

        experiment.fail(error)

        hypothesis = self.state.get_hypothesis_by_id(experiment.hypothesis_id)
        if hypothesis:
            hypothesis.status = HypothesisStatus.inconclusive
            hypothesis.result_summary = f"Experiment failed: {error}"

        logger.info(f"Experiment {experiment_id} failed: {error}")

    def mark_hypothesis_deduped(self, hypothesis_id: str, duplicate_of: str) -> None:
        """Mark a hypothesis as duplicate of another."""
        hypothesis = self.state.get_hypothesis_by_id(hypothesis_id)
        if hypothesis is None:
            return

        hypothesis.status = HypothesisStatus.deduped
        hypothesis.result_summary = f"Duplicate of {duplicate_of}"
        logger.info(f"Hypothesis {hypothesis_id} marked as duplicate of {duplicate_of}")

    def get_next_hypotheses(self, limit: int | None = None) -> list[Hypothesis]:
        """Get the next hypotheses to test, ordered by priority."""
        max_limit = limit or self.state.max_hypotheses_per_iteration
        return self.state.get_queued_hypotheses(max_limit)

    def get_metrics(self) -> dict[str, Any]:
        """Get current discovery metrics."""
        metrics = self.state.update_metrics()
        return metrics.model_dump()
