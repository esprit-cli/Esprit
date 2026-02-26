"""Tests for discovery engine data models."""

from esprit.discovery.models import (
    AnomalyEvent,
    AnomalyType,
    DiscoveryState,
    EvidenceRef,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
)


class TestHypothesis:
    def test_create_hypothesis(self):
        h = Hypothesis(
            title="IDOR on invoice endpoint",
            source="proxy_anomaly",
            target="/api/invoices/{id}",
            vulnerability_class="IDOR",
            novelty_score=0.82,
            impact_score=0.70,
            evidence_score=0.5,
            reachability_score=0.9,
        )
        assert h.id.startswith("hyp_")
        assert h.status == HypothesisStatus.queued
        assert h.title == "IDOR on invoice endpoint"

    def test_compute_priority(self):
        h = Hypothesis(
            title="Test",
            source="test",
            target="/test",
            novelty_score=1.0,
            impact_score=1.0,
            evidence_score=1.0,
            reachability_score=1.0,
        )
        priority = h.compute_priority()
        assert abs(priority - 1.0) < 1e-9  # 0.35 + 0.30 + 0.20 + 0.15

    def test_compute_priority_weighted(self):
        h = Hypothesis(
            title="Test",
            source="test",
            target="/test",
            novelty_score=0.5,
            impact_score=0.0,
            evidence_score=0.0,
            reachability_score=0.0,
        )
        priority = h.compute_priority()
        assert abs(priority - 0.175) < 0.001


class TestExperiment:
    def test_start_experiment(self):
        e = Experiment(hypothesis_id="hyp_test")
        e.start("agent_1")
        assert e.status == ExperimentStatus.running
        assert e.agent_id == "agent_1"
        assert e.started_at is not None

    def test_complete_experiment(self):
        e = Experiment(hypothesis_id="hyp_test")
        e.start("agent_1")
        evidence = [EvidenceRef(source="proxy", ref_id="req_123")]
        e.complete("validated", evidence)
        assert e.status == ExperimentStatus.completed
        assert e.result == "validated"
        assert len(e.evidence_refs) == 1

    def test_fail_experiment(self):
        e = Experiment(hypothesis_id="hyp_test")
        e.start("agent_1")
        e.fail("Connection refused")
        assert e.status == ExperimentStatus.failed
        assert e.error == "Connection refused"


class TestAnomalyEvent:
    def test_create_anomaly(self):
        a = AnomalyEvent(
            anomaly_type=AnomalyType.status_code,
            source_tool="proxy",
            description="500 error on /api/users",
            target="/api/users",
        )
        assert a.id.startswith("anom_")
        assert a.anomaly_type == AnomalyType.status_code


class TestDiscoveryState:
    def test_add_hypothesis(self):
        state = DiscoveryState()
        h = Hypothesis(
            title="Test IDOR",
            source="proxy",
            target="/api/test",
            novelty_score=0.8,
            impact_score=0.7,
        )
        hid = state.add_hypothesis(h)
        assert hid == h.id
        assert len(state.hypotheses) == 1
        assert h.priority > 0

    def test_get_queued_hypotheses_ordered(self):
        state = DiscoveryState()
        h1 = Hypothesis(title="Low", source="test", target="/low", novelty_score=0.1)
        h2 = Hypothesis(title="High", source="test", target="/high", novelty_score=0.9)
        state.add_hypothesis(h1)
        state.add_hypothesis(h2)
        queued = state.get_queued_hypotheses(limit=2)
        assert queued[0].title == "High"

    def test_update_metrics(self):
        state = DiscoveryState()
        h = Hypothesis(title="Test", source="test", target="/test")
        state.add_hypothesis(h)
        metrics = state.update_metrics()
        assert metrics.total_hypotheses == 1
        assert metrics.queued_hypotheses == 1

    def test_to_persistence_dict(self):
        state = DiscoveryState()
        h = Hypothesis(title="Test", source="test", target="/test")
        state.add_hypothesis(h)
        data = state.to_persistence_dict()
        assert "hypotheses" in data
        assert len(data["hypotheses"]) == 1

    def test_max_concurrent_experiments(self):
        state = DiscoveryState(max_concurrent_experiments=1)
        assert state.get_running_experiments_count() == 0
        e = Experiment(hypothesis_id="hyp_test")
        e.start("agent_1")
        state.add_experiment(e)
        assert state.get_running_experiments_count() == 1
