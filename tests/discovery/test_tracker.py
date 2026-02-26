"""Tests for discovery tracker lifecycle management."""

from esprit.discovery.models import (
    EvidenceRef,
    Hypothesis,
    HypothesisStatus,
)
from esprit.discovery.tracker import DiscoveryTracker


class TestDiscoveryTracker:
    def test_submit_hypothesis(self):
        tracker = DiscoveryTracker()
        h = Hypothesis(
            title="Test IDOR",
            source="proxy",
            target="/api/users/{id}",
            novelty_score=0.8,
        )
        hid = tracker.submit_hypothesis(h)
        assert hid.startswith("hyp_")
        assert len(tracker.state.hypotheses) == 1

    def test_start_experiment(self):
        tracker = DiscoveryTracker()
        h = Hypothesis(title="Test", source="test", target="/test")
        hid = tracker.submit_hypothesis(h)

        eid = tracker.start_experiment(hid, "agent_1", "Test IDOR on /test")
        assert eid is not None
        assert eid.startswith("exp_")
        assert h.status == HypothesisStatus.in_progress

    def test_complete_experiment_validated(self):
        tracker = DiscoveryTracker()
        h = Hypothesis(title="Test", source="test", target="/test")
        hid = tracker.submit_hypothesis(h)
        eid = tracker.start_experiment(hid, "agent_1", "Test task")

        evidence = [EvidenceRef(source="proxy", ref_id="req_1")]
        tracker.complete_experiment(eid, "validated", evidence)
        assert h.status == HypothesisStatus.validated

    def test_complete_experiment_falsified(self):
        tracker = DiscoveryTracker()
        h = Hypothesis(title="Test", source="test", target="/test")
        hid = tracker.submit_hypothesis(h)
        eid = tracker.start_experiment(hid, "agent_1", "Test task")

        tracker.complete_experiment(eid, "falsified")
        assert h.status == HypothesisStatus.falsified

    def test_fail_experiment(self):
        tracker = DiscoveryTracker()
        h = Hypothesis(title="Test", source="test", target="/test")
        hid = tracker.submit_hypothesis(h)
        eid = tracker.start_experiment(hid, "agent_1", "Test task")

        tracker.fail_experiment(eid, "Timeout")
        assert h.status == HypothesisStatus.inconclusive

    def test_max_concurrent_experiments_enforced(self):
        tracker = DiscoveryTracker()
        tracker.state.max_concurrent_experiments = 1

        h1 = Hypothesis(title="H1", source="test", target="/t1")
        h2 = Hypothesis(title="H2", source="test", target="/t2")
        hid1 = tracker.submit_hypothesis(h1)
        hid2 = tracker.submit_hypothesis(h2)

        eid1 = tracker.start_experiment(hid1, "agent_1", "Task 1")
        assert eid1 is not None

        eid2 = tracker.start_experiment(hid2, "agent_2", "Task 2")
        assert eid2 is None  # should be blocked

    def test_mark_deduped(self):
        tracker = DiscoveryTracker()
        h1 = Hypothesis(title="H1", source="test", target="/test")
        h2 = Hypothesis(title="H2", source="test", target="/test")
        hid1 = tracker.submit_hypothesis(h1)
        hid2 = tracker.submit_hypothesis(h2)

        tracker.mark_hypothesis_deduped(hid2, hid1)
        assert h2.status == HypothesisStatus.deduped

    def test_get_next_hypotheses(self):
        tracker = DiscoveryTracker()
        h1 = Hypothesis(title="Low", source="test", target="/low", novelty_score=0.1)
        h2 = Hypothesis(title="High", source="test", target="/high", novelty_score=0.9)
        tracker.submit_hypothesis(h1)
        tracker.submit_hypothesis(h2)

        next_hyps = tracker.get_next_hypotheses(limit=1)
        assert len(next_hyps) == 1
        assert next_hyps[0].title == "High"

    def test_get_metrics(self):
        tracker = DiscoveryTracker()
        h = Hypothesis(title="Test", source="test", target="/test")
        tracker.submit_hypothesis(h)
        metrics = tracker.get_metrics()
        assert metrics["total_hypotheses"] == 1
