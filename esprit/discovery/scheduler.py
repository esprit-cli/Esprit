"""Schedule experiments as subagent tasks using the existing agent graph.

The scheduler selects top-priority hypotheses and generates focused task
descriptions for subagents, respecting budget limits.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import DiscoveryState, Experiment, HypothesisStatus
from .prioritizer import HypothesisPrioritizer


logger = logging.getLogger(__name__)


class ExperimentScheduler:
    """Schedule hypothesis experiments as subagent tasks."""

    def __init__(self, state: DiscoveryState) -> None:
        self._state = state
        self._prioritizer = HypothesisPrioritizer(state)

    def get_next_tasks(self, max_tasks: int | None = None) -> list[dict[str, Any]]:
        """Generate the next batch of subagent task descriptions.

        Returns a list of task dicts ready for `create_agent`, each with:
        - hypothesis_id: str
        - task_description: str
        - suggested_name: str
        - suggested_skills: list[str]
        """
        available_slots = self._state.max_concurrent_experiments - (
            self._state.get_running_experiments_count()
        )
        if available_slots <= 0:
            return []

        if max_tasks is not None:
            available_slots = min(available_slots, max_tasks)

        ranked = self._prioritizer.rank_queued(limit=available_slots)
        tasks: list[dict[str, Any]] = []

        for hypothesis in ranked:
            task = self._build_task(hypothesis.id)
            if task is not None:
                tasks.append(task)

        return tasks

    def _build_task(self, hypothesis_id: str) -> dict[str, Any] | None:
        """Build a subagent task description for a hypothesis."""
        hypothesis = self._state.get_hypothesis_by_id(hypothesis_id)
        if hypothesis is None:
            return None

        if hypothesis.status != HypothesisStatus.queued:
            return None

        vuln_class = hypothesis.vulnerability_class
        target = hypothesis.target
        evidence_summary = ""
        if hypothesis.evidence_refs:
            refs = [f"{e.source}:{e.ref_id}" for e in hypothesis.evidence_refs[:3]]
            evidence_summary = f"\nEvidence references: {', '.join(refs)}"

        task_description = (
            f"Investigate and validate the following security hypothesis:\n\n"
            f"Hypothesis: {hypothesis.title}\n"
            f"Target: {target}\n"
            f"Vulnerability Class: {vuln_class}\n"
            f"Confidence: {hypothesis.confidence:.0%}\n"
            f"Priority Score: {hypothesis.priority:.3f}\n"
            f"{evidence_summary}\n\n"
            f"Instructions:\n"
            f"1. Reproduce the observed anomaly on the target.\n"
            f"2. Attempt to confirm or deny the vulnerability.\n"
            f"3. If confirmed, document the proof of concept.\n"
            f"4. Report findings via agent_finish with:\n"
            f'   - success=true and findings if validated\n'
            f'   - success=false with explanation if falsified\n'
            f"5. Do NOT create a vulnerability report — the parent agent handles that.\n"
        )

        suggested_skills = _suggest_skills(vuln_class)

        return {
            "hypothesis_id": hypothesis_id,
            "task_description": task_description,
            "suggested_name": f"Discovery: {vuln_class} on {_truncate(target, 40)}",
            "suggested_skills": suggested_skills,
        }

    def mark_scheduled(self, hypothesis_id: str, agent_id: str) -> str | None:
        """Mark a hypothesis as in-progress and create an experiment record."""
        hypothesis = self._state.get_hypothesis_by_id(hypothesis_id)
        if hypothesis is None:
            return None

        experiment = Experiment(
            hypothesis_id=hypothesis_id,
            task_description=f"Validate: {hypothesis.title}",
        )
        experiment.start(agent_id)
        experiment_id = self._state.add_experiment(experiment)

        hypothesis.status = HypothesisStatus.in_progress
        hypothesis.experiment_id = experiment_id

        logger.info(f"Scheduled experiment {experiment_id} for hypothesis {hypothesis_id}")
        return experiment_id

    def has_pending_work(self) -> bool:
        """Check if there are queued hypotheses that can be scheduled."""
        if self._state.get_running_experiments_count() >= self._state.max_concurrent_experiments:
            return False
        return bool(self._prioritizer.rank_queued(limit=1))

    def get_schedule_summary(self) -> dict[str, Any]:
        """Get a summary of the scheduling state."""
        running = self._state.get_running_experiments_count()
        queued_count = len(
            [h for h in self._state.hypotheses if h.status == HypothesisStatus.queued]
        )
        return {
            "queued_hypotheses": queued_count,
            "running_experiments": running,
            "max_concurrent": self._state.max_concurrent_experiments,
            "available_slots": max(0, self._state.max_concurrent_experiments - running),
            "has_pending_work": self.has_pending_work(),
        }


def _suggest_skills(vuln_class: str) -> list[str]:
    """Map vulnerability class to relevant agent skills."""
    vuln_lower = vuln_class.lower()
    skills: list[str] = []

    skill_map = {
        "idor": ["idor"],
        "authorization": ["broken_function_level_authorization", "idor"],
        "injection": ["sql_injection"],
        "sql": ["sql_injection"],
        "xss": ["xss"],
        "ssrf": ["ssrf"],
        "rce": ["rce"],
        "information disclosure": ["information_disclosure"],
        "authentication": ["authentication_jwt"],
        "csrf": ["csrf"],
        "path traversal": ["path_traversal_lfi_rfi"],
        "redirect": ["open_redirect"],
        "race": ["race_conditions"],
        "xxe": ["xxe"],
        "file upload": ["insecure_file_uploads"],
        "mass assignment": ["mass_assignment"],
    }

    for keyword, mapped_skills in skill_map.items():
        if keyword in vuln_lower:
            skills.extend(mapped_skills)

    return skills[:3]  # cap at 3 skills


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."
