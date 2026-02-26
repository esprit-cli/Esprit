"""Generate hypotheses from anomaly events.

Converts observed anomalies into testable security hypotheses with initial
scoring estimates.
"""

from __future__ import annotations

import logging
from typing import Any

from .models import (
    AnomalyEvent,
    AnomalyType,
    DiscoveryState,
    EvidenceRef,
    Hypothesis,
    HypothesisStatus,
)


logger = logging.getLogger(__name__)

# Maps anomaly types to likely vulnerability classes and base impact scores
_ANOMALY_VULN_MAP: dict[AnomalyType, list[dict[str, Any]]] = {
    AnomalyType.status_code: [
        {
            "status_codes": {401, 403},
            "vuln_class": "Authorization Bypass",
            "base_impact": 0.75,
            "base_evidence": 0.4,
        },
        {
            "status_codes": {500, 502, 503},
            "vuln_class": "Server Error",
            "base_impact": 0.5,
            "base_evidence": 0.3,
        },
        {
            "status_codes": {405},
            "vuln_class": "Method Tampering",
            "base_impact": 0.4,
            "base_evidence": 0.3,
        },
    ],
    AnomalyType.error_leak: [
        {
            "vuln_class": "Information Disclosure",
            "base_impact": 0.55,
            "base_evidence": 0.6,
        },
    ],
    AnomalyType.injection_signal: [
        {
            "vuln_class": "Injection",
            "base_impact": 0.85,
            "base_evidence": 0.5,
        },
    ],
    AnomalyType.timing: [
        {
            "vuln_class": "Timing Side-Channel",
            "base_impact": 0.45,
            "base_evidence": 0.35,
        },
    ],
    AnomalyType.auth_bypass: [
        {
            "vuln_class": "Authentication Bypass",
            "base_impact": 0.90,
            "base_evidence": 0.5,
        },
    ],
    AnomalyType.unexpected_data: [
        {
            "vuln_class": "Information Disclosure",
            "base_impact": 0.50,
            "base_evidence": 0.4,
        },
    ],
    AnomalyType.response_diff: [
        {
            "vuln_class": "IDOR",
            "base_impact": 0.70,
            "base_evidence": 0.45,
        },
    ],
}


class HypothesisGenerator:
    """Generates testable hypotheses from anomaly events."""

    def __init__(self, state: DiscoveryState) -> None:
        self._state = state

    def generate_from_anomaly(self, anomaly: AnomalyEvent) -> list[Hypothesis]:
        """Generate hypotheses from a single anomaly event."""
        vuln_mappings = _ANOMALY_VULN_MAP.get(anomaly.anomaly_type, [])
        if not vuln_mappings:
            return []

        hypotheses: list[Hypothesis] = []
        raw = anomaly.raw_data

        for mapping in vuln_mappings:
            # For status_code anomalies, check if the status code matches
            if "status_codes" in mapping:
                status = raw.get("status_code")
                if isinstance(status, int) and status not in mapping["status_codes"]:
                    continue

            # Check if a similar hypothesis already exists
            if self._is_duplicate(anomaly.target, mapping["vuln_class"]):
                continue

            hypothesis = Hypothesis(
                title=self._generate_title(anomaly, mapping["vuln_class"]),
                source=f"{anomaly.source_tool}_{anomaly.anomaly_type.value}",
                target=anomaly.target,
                vulnerability_class=mapping["vuln_class"],
                impact_score=mapping["base_impact"],
                evidence_score=mapping["base_evidence"],
                novelty_score=self._compute_novelty(anomaly.target, mapping["vuln_class"]),
                reachability_score=self._compute_reachability(anomaly),
                evidence_refs=list(anomaly.evidence_refs),
            )

            hypotheses.append(hypothesis)
            anomaly.generated_hypothesis_ids.append(hypothesis.id)

        return hypotheses

    def generate_from_anomalies(self, anomalies: list[AnomalyEvent]) -> list[Hypothesis]:
        """Generate hypotheses from a batch of anomaly events."""
        all_hypotheses: list[Hypothesis] = []
        max_per_batch = self._state.max_hypotheses_per_iteration

        for anomaly in anomalies:
            if len(all_hypotheses) >= max_per_batch:
                break

            new_hypotheses = self.generate_from_anomaly(anomaly)
            remaining = max_per_batch - len(all_hypotheses)
            all_hypotheses.extend(new_hypotheses[:remaining])

        return all_hypotheses

    def _generate_title(self, anomaly: AnomalyEvent, vuln_class: str) -> str:
        """Generate a concise hypothesis title."""
        target = anomaly.target
        if len(target) > 60:
            target = target[:57] + "..."
        return f"Potential {vuln_class} on {target}"

    def _is_duplicate(self, target: str, vuln_class: str) -> bool:
        """Check if a similar hypothesis already exists in state."""
        target_normalized = _normalize_target(target)
        for existing in self._state.hypotheses:
            if (
                existing.vulnerability_class == vuln_class
                and _normalize_target(existing.target) == target_normalized
                and existing.status != HypothesisStatus.deduped
            ):
                return True
        return False

    def _compute_novelty(self, target: str, vuln_class: str) -> float:
        """Compute novelty score based on existing hypotheses and findings."""
        if not self._state.hypotheses:
            return 0.9  # first hypothesis is always novel

        target_normalized = _normalize_target(target)
        same_target_count = sum(
            1
            for h in self._state.hypotheses
            if _normalize_target(h.target) == target_normalized
        )
        same_class_count = sum(
            1
            for h in self._state.hypotheses
            if h.vulnerability_class == vuln_class
        )

        # Penalize repeated targets and vuln classes
        target_penalty = min(same_target_count * 0.2, 0.6)
        class_penalty = min(same_class_count * 0.1, 0.3)

        return max(0.1, 0.9 - target_penalty - class_penalty)

    def _compute_reachability(self, anomaly: AnomalyEvent) -> float:
        """Estimate how likely we can test this hypothesis with available tools."""
        # Proxy-based anomalies are highly reachable (we can replay requests)
        if anomaly.source_tool == "proxy":
            return 0.9

        # Browser-based anomalies are somewhat reachable
        if anomaly.source_tool == "browser":
            return 0.7

        # Terminal-based need more setup
        if anomaly.source_tool == "terminal":
            return 0.6

        return 0.5


def _normalize_target(target: str) -> str:
    """Normalize a target string for comparison.

    Strips method prefix, lowercases, and removes dynamic path segments.
    """
    target = target.strip().lower()

    # Remove HTTP method prefix (e.g., "GET /api/users" -> "/api/users")
    for method in ("get ", "post ", "put ", "delete ", "patch ", "head ", "options "):
        if target.startswith(method):
            target = target[len(method):]
            break

    # Replace UUID-like segments with placeholder
    import re

    target = re.sub(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "/{id}",
        target,
    )
    # Replace numeric IDs
    target = re.sub(r"/\d+(?=/|$)", "/{id}", target)

    return target
