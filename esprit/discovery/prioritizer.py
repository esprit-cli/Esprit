"""Prioritize and rank hypotheses for experiment scheduling.

Provides deduplication at the hypothesis level (before report-time dedupe)
and ordering by composite priority score.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import DiscoveryState, Hypothesis, HypothesisStatus


logger = logging.getLogger(__name__)


class HypothesisPrioritizer:
    """Rank and deduplicate hypotheses before experiment scheduling."""

    def __init__(self, state: DiscoveryState) -> None:
        self._state = state

    def rank_queued(self, limit: int | None = None) -> list[Hypothesis]:
        """Return queued hypotheses sorted by priority (highest first)."""
        queued = [h for h in self._state.hypotheses if h.status == HypothesisStatus.queued]

        # Recompute priorities
        for h in queued:
            h.compute_priority()

        ranked = sorted(queued, key=lambda h: h.priority, reverse=True)
        if limit is not None:
            ranked = ranked[:limit]
        return ranked

    def deduplicate_new_hypotheses(
        self, new_hypotheses: list[Hypothesis]
    ) -> list[Hypothesis]:
        """Filter out hypotheses that duplicate existing ones in state.

        Deduplication is based on normalized target + vulnerability class.
        Duplicates are added to state with status=deduped for tracking.
        """
        accepted: list[Hypothesis] = []

        for hypothesis in new_hypotheses:
            duplicate_of = self._find_duplicate(hypothesis)
            if duplicate_of is not None:
                hypothesis.status = HypothesisStatus.deduped
                hypothesis.result_summary = f"Duplicate of {duplicate_of}"
                self._state.add_hypothesis(hypothesis)
                logger.debug(
                    f"Hypothesis '{hypothesis.title}' deduped against {duplicate_of}"
                )
            else:
                accepted.append(hypothesis)

        return accepted

    def _find_duplicate(self, hypothesis: Hypothesis) -> str | None:
        """Check if a hypothesis is a duplicate of an existing one.

        Returns the ID of the duplicate, or None.
        """
        target_norm = _normalize_for_dedupe(hypothesis.target)
        vuln_class = hypothesis.vulnerability_class.lower().strip()

        for existing in self._state.hypotheses:
            if existing.status == HypothesisStatus.deduped:
                continue

            existing_target = _normalize_for_dedupe(existing.target)
            existing_class = existing.vulnerability_class.lower().strip()

            if existing_target == target_norm and existing_class == vuln_class:
                return existing.id

        return None

    def get_priority_summary(self) -> dict[str, Any]:
        """Get a summary of hypothesis priorities for observability."""
        queued = self.rank_queued()
        return {
            "total_queued": len(queued),
            "top_5": [
                {
                    "id": h.id,
                    "title": h.title,
                    "priority": round(h.priority, 3),
                    "vulnerability_class": h.vulnerability_class,
                }
                for h in queued[:5]
            ],
        }


def _normalize_for_dedupe(target: str) -> str:
    """Normalize target for deduplication comparison."""
    import re

    target = target.strip().lower()
    # Remove HTTP method prefix
    for method in ("get ", "post ", "put ", "delete ", "patch ", "head ", "options "):
        if target.startswith(method):
            target = target[len(method):]
            break
    # Replace numeric IDs
    target = re.sub(r"/\d+(?=/|$)", "/{id}", target)
    # Replace UUIDs
    target = re.sub(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "/{id}",
        target,
    )
    return target
