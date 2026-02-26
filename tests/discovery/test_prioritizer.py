"""Tests for hypothesis prioritization and deduplication."""

from esprit.discovery.models import DiscoveryState, Hypothesis, HypothesisStatus
from esprit.discovery.prioritizer import HypothesisPrioritizer


class TestPrioritizer:
    def test_rank_by_priority(self):
        state = DiscoveryState()
        h1 = Hypothesis(
            title="Low", source="test", target="/low",
            novelty_score=0.1, impact_score=0.1,
        )
        h2 = Hypothesis(
            title="High", source="test", target="/high",
            novelty_score=0.9, impact_score=0.9,
        )
        state.add_hypothesis(h1)
        state.add_hypothesis(h2)

        prioritizer = HypothesisPrioritizer(state)
        ranked = prioritizer.rank_queued()
        assert ranked[0].title == "High"
        assert ranked[1].title == "Low"

    def test_rank_limit(self):
        state = DiscoveryState()
        for i in range(10):
            h = Hypothesis(title=f"H{i}", source="test", target=f"/t{i}", novelty_score=i / 10)
            state.add_hypothesis(h)

        prioritizer = HypothesisPrioritizer(state)
        ranked = prioritizer.rank_queued(limit=3)
        assert len(ranked) == 3

    def test_excludes_non_queued(self):
        state = DiscoveryState()
        h1 = Hypothesis(title="Queued", source="test", target="/q", novelty_score=0.5)
        h2 = Hypothesis(title="Done", source="test", target="/d", novelty_score=0.9)
        h2.status = HypothesisStatus.validated
        state.add_hypothesis(h1)
        state.hypotheses.append(h2)

        prioritizer = HypothesisPrioritizer(state)
        ranked = prioritizer.rank_queued()
        assert len(ranked) == 1
        assert ranked[0].title == "Queued"


class TestDeduplication:
    def test_deduplicate_same_target_and_class(self):
        state = DiscoveryState()
        existing = Hypothesis(
            title="Existing", source="test", target="/api/users",
            vulnerability_class="IDOR",
        )
        state.add_hypothesis(existing)

        prioritizer = HypothesisPrioritizer(state)
        new_hyps = [
            Hypothesis(
                title="New", source="test", target="/api/users",
                vulnerability_class="IDOR",
            )
        ]
        accepted = prioritizer.deduplicate_new_hypotheses(new_hyps)
        assert len(accepted) == 0
        # The deduped one should be in state
        deduped = [h for h in state.hypotheses if h.status == HypothesisStatus.deduped]
        assert len(deduped) == 1

    def test_different_class_not_deduped(self):
        state = DiscoveryState()
        existing = Hypothesis(
            title="Existing", source="test", target="/api/users",
            vulnerability_class="IDOR",
        )
        state.add_hypothesis(existing)

        prioritizer = HypothesisPrioritizer(state)
        new_hyps = [
            Hypothesis(
                title="New SQLi", source="test", target="/api/users",
                vulnerability_class="Injection",
            )
        ]
        accepted = prioritizer.deduplicate_new_hypotheses(new_hyps)
        assert len(accepted) == 1

    def test_different_target_not_deduped(self):
        state = DiscoveryState()
        existing = Hypothesis(
            title="Existing", source="test", target="/api/users",
            vulnerability_class="IDOR",
        )
        state.add_hypothesis(existing)

        prioritizer = HypothesisPrioritizer(state)
        new_hyps = [
            Hypothesis(
                title="New", source="test", target="/api/orders",
                vulnerability_class="IDOR",
            )
        ]
        accepted = prioritizer.deduplicate_new_hypotheses(new_hyps)
        assert len(accepted) == 1

    def test_numeric_id_normalization_dedupes(self):
        state = DiscoveryState()
        existing = Hypothesis(
            title="Existing", source="test", target="/api/users/123",
            vulnerability_class="IDOR",
        )
        state.add_hypothesis(existing)

        prioritizer = HypothesisPrioritizer(state)
        new_hyps = [
            Hypothesis(
                title="New", source="test", target="/api/users/456",
                vulnerability_class="IDOR",
            )
        ]
        accepted = prioritizer.deduplicate_new_hypotheses(new_hyps)
        assert len(accepted) == 0

    def test_priority_summary(self):
        state = DiscoveryState()
        h = Hypothesis(
            title="Test", source="test", target="/test",
            vulnerability_class="XSS", novelty_score=0.5,
        )
        state.add_hypothesis(h)

        prioritizer = HypothesisPrioritizer(state)
        summary = prioritizer.get_priority_summary()
        assert summary["total_queued"] == 1
        assert len(summary["top_5"]) == 1
