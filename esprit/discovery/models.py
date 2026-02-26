"""Data models for the autonomous discovery engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class HypothesisStatus(str, Enum):
    queued = "queued"
    in_progress = "in_progress"
    validated = "validated"
    falsified = "falsified"
    inconclusive = "inconclusive"
    deduped = "deduped"


class ExperimentStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class AnomalyType(str, Enum):
    status_code = "status_code"
    response_diff = "response_diff"
    timing = "timing"
    error_leak = "error_leak"
    auth_bypass = "auth_bypass"
    injection_signal = "injection_signal"
    unexpected_data = "unexpected_data"


def _generate_hypothesis_id() -> str:
    return f"hyp_{uuid.uuid4().hex[:8]}"


def _generate_experiment_id() -> str:
    return f"exp_{uuid.uuid4().hex[:8]}"


def _generate_anomaly_id() -> str:
    return f"anom_{uuid.uuid4().hex[:8]}"


class EvidenceRef(BaseModel):
    """Reference to a raw evidence artifact."""

    source: str  # e.g., "proxy", "browser", "terminal", "python"
    ref_id: str  # e.g., request ID, execution ID, screenshot path
    description: str = ""
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class Hypothesis(BaseModel):
    """A testable security hypothesis derived from observed signals."""

    id: str = Field(default_factory=_generate_hypothesis_id)
    title: str
    source: str  # e.g., "proxy_anomaly", "static_analysis", "endpoint_enumeration"
    target: str  # e.g., "/api/invoices/{id}"
    vulnerability_class: str = ""  # e.g., "IDOR", "SQLi", "XSS"
    novelty_score: float = 0.0
    impact_score: float = 0.0
    evidence_score: float = 0.0
    reachability_score: float = 0.0
    confidence: float = 0.0
    priority: float = 0.0
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    status: HypothesisStatus = HypothesisStatus.queued
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    experiment_id: str | None = None
    result_summary: str = ""
    parent_hypothesis_id: str | None = None

    def compute_priority(self) -> float:
        """Compute priority score using weighted formula."""
        self.priority = (
            0.35 * self.novelty_score
            + 0.30 * self.impact_score
            + 0.20 * self.evidence_score
            + 0.15 * self.reachability_score
        )
        self.updated_at = datetime.now(UTC).isoformat()
        return self.priority


class Experiment(BaseModel):
    """A concrete test action to validate or falsify a hypothesis."""

    id: str = Field(default_factory=_generate_experiment_id)
    hypothesis_id: str
    agent_id: str | None = None  # subagent assigned to this experiment
    task_description: str = ""
    status: ExperimentStatus = ExperimentStatus.pending
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    started_at: str | None = None
    completed_at: str | None = None
    result: str = ""  # "validated", "falsified", "inconclusive", or details
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    error: str | None = None

    def start(self, agent_id: str) -> None:
        self.agent_id = agent_id
        self.status = ExperimentStatus.running
        self.started_at = datetime.now(UTC).isoformat()

    def complete(self, result: str, evidence: list[EvidenceRef] | None = None) -> None:
        self.status = ExperimentStatus.completed
        self.completed_at = datetime.now(UTC).isoformat()
        self.result = result
        if evidence:
            self.evidence_refs.extend(evidence)

    def fail(self, error: str) -> None:
        self.status = ExperimentStatus.failed
        self.completed_at = datetime.now(UTC).isoformat()
        self.error = error


class AnomalyEvent(BaseModel):
    """An observed anomaly that may generate hypotheses."""

    id: str = Field(default_factory=_generate_anomaly_id)
    anomaly_type: AnomalyType
    source_tool: str  # e.g., "proxy", "browser", "terminal"
    description: str
    target: str = ""  # endpoint or resource
    raw_data: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    generated_hypothesis_ids: list[str] = Field(default_factory=list)
    timestamp: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class DiscoveryMetrics(BaseModel):
    """Aggregate metrics for the discovery engine."""

    total_hypotheses: int = 0
    queued_hypotheses: int = 0
    validated_hypotheses: int = 0
    falsified_hypotheses: int = 0
    inconclusive_hypotheses: int = 0
    deduped_hypotheses: int = 0
    total_experiments: int = 0
    completed_experiments: int = 0
    failed_experiments: int = 0
    total_anomalies: int = 0
    hypothesis_conversion_rate: float = 0.0
    novelty_ratio: float = 0.0

    def update_from_state(self, state: "DiscoveryState") -> None:
        self.total_hypotheses = len(state.hypotheses)
        self.queued_hypotheses = sum(
            1 for h in state.hypotheses if h.status == HypothesisStatus.queued
        )
        self.validated_hypotheses = sum(
            1 for h in state.hypotheses if h.status == HypothesisStatus.validated
        )
        self.falsified_hypotheses = sum(
            1 for h in state.hypotheses if h.status == HypothesisStatus.falsified
        )
        self.inconclusive_hypotheses = sum(
            1 for h in state.hypotheses if h.status == HypothesisStatus.inconclusive
        )
        self.deduped_hypotheses = sum(
            1 for h in state.hypotheses if h.status == HypothesisStatus.deduped
        )
        self.total_experiments = len(state.experiments)
        self.completed_experiments = sum(
            1 for e in state.experiments if e.status == ExperimentStatus.completed
        )
        self.failed_experiments = sum(
            1 for e in state.experiments if e.status == ExperimentStatus.failed
        )
        self.total_anomalies = len(state.anomaly_events)
        tested = self.validated_hypotheses + self.falsified_hypotheses + self.inconclusive_hypotheses
        self.hypothesis_conversion_rate = (
            self.validated_hypotheses / tested if tested > 0 else 0.0
        )
        if self.total_hypotheses > 0:
            self.novelty_ratio = (
                (self.total_hypotheses - self.deduped_hypotheses) / self.total_hypotheses
            )


class DiscoveryState(BaseModel):
    """Complete discovery engine state, composable with AgentState."""

    hypotheses: list[Hypothesis] = Field(default_factory=list)
    experiments: list[Experiment] = Field(default_factory=list)
    anomaly_events: list[AnomalyEvent] = Field(default_factory=list)
    discovery_metrics: DiscoveryMetrics = Field(default_factory=DiscoveryMetrics)
    evidence_index: dict[str, EvidenceRef] = Field(default_factory=dict)
    max_hypotheses_per_iteration: int = 5
    max_concurrent_experiments: int = 3
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

    def add_hypothesis(self, hypothesis: Hypothesis) -> str:
        hypothesis.compute_priority()
        self.hypotheses.append(hypothesis)
        self.updated_at = datetime.now(UTC).isoformat()
        return hypothesis.id

    def add_experiment(self, experiment: Experiment) -> str:
        self.experiments.append(experiment)
        self.updated_at = datetime.now(UTC).isoformat()
        return experiment.id

    def add_anomaly(self, anomaly: AnomalyEvent) -> str:
        self.anomaly_events.append(anomaly)
        self.updated_at = datetime.now(UTC).isoformat()
        return anomaly.id

    def add_evidence(self, key: str, ref: EvidenceRef) -> None:
        self.evidence_index[key] = ref
        self.updated_at = datetime.now(UTC).isoformat()

    def get_queued_hypotheses(self, limit: int = 5) -> list[Hypothesis]:
        queued = [h for h in self.hypotheses if h.status == HypothesisStatus.queued]
        return sorted(queued, key=lambda h: h.priority, reverse=True)[:limit]

    def get_running_experiments_count(self) -> int:
        return sum(1 for e in self.experiments if e.status == ExperimentStatus.running)

    def get_hypothesis_by_id(self, hypothesis_id: str) -> Hypothesis | None:
        for h in self.hypotheses:
            if h.id == hypothesis_id:
                return h
        return None

    def get_experiment_by_id(self, experiment_id: str) -> Experiment | None:
        for e in self.experiments:
            if e.id == experiment_id:
                return e
        return None

    def update_metrics(self) -> DiscoveryMetrics:
        self.discovery_metrics.update_from_state(self)
        self.updated_at = datetime.now(UTC).isoformat()
        return self.discovery_metrics

    def to_persistence_dict(self) -> dict[str, Any]:
        """Serialize for tracer persistence."""
        return {
            "hypotheses": [h.model_dump() for h in self.hypotheses],
            "experiments": [e.model_dump() for e in self.experiments],
            "anomaly_events": [a.model_dump() for a in self.anomaly_events],
            "discovery_metrics": self.discovery_metrics.model_dump(),
            "evidence_index": {k: v.model_dump() for k, v in self.evidence_index.items()},
        }
