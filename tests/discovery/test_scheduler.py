"""Tests for experiment scheduling."""

from esprit.discovery.models import (
    DiscoveryState,
    Experiment,
    ExperimentStatus,
    Hypothesis,
    HypothesisStatus,
)
from esprit.discovery.scheduler import ExperimentScheduler, _suggest_skills


class TestScheduler:
    def test_get_next_tasks(self):
        state = DiscoveryState(max_concurrent_experiments=3)
        h = Hypothesis(
            title="IDOR on /api/users",
            source="proxy",
            target="/api/users/{id}",
            vulnerability_class="IDOR",
            novelty_score=0.8,
            impact_score=0.7,
        )
        state.add_hypothesis(h)

        scheduler = ExperimentScheduler(state)
        tasks = scheduler.get_next_tasks()
        assert len(tasks) == 1
        assert tasks[0]["hypothesis_id"] == h.id
        assert "IDOR" in tasks[0]["task_description"]
        assert "idor" in tasks[0]["suggested_skills"]

    def test_respects_concurrent_limit(self):
        state = DiscoveryState(max_concurrent_experiments=1)
        h1 = Hypothesis(title="H1", source="test", target="/t1", vulnerability_class="XSS")
        h2 = Hypothesis(title="H2", source="test", target="/t2", vulnerability_class="IDOR")
        state.add_hypothesis(h1)
        state.add_hypothesis(h2)

        # Create a running experiment
        exp = Experiment(hypothesis_id=h1.id)
        exp.start("agent_1")
        state.add_experiment(exp)
        h1.status = HypothesisStatus.in_progress

        scheduler = ExperimentScheduler(state)
        tasks = scheduler.get_next_tasks()
        assert len(tasks) == 0

    def test_max_tasks_param(self):
        state = DiscoveryState(max_concurrent_experiments=10)
        for i in range(5):
            h = Hypothesis(
                title=f"H{i}", source="test", target=f"/t{i}",
                vulnerability_class="XSS",
            )
            state.add_hypothesis(h)

        scheduler = ExperimentScheduler(state)
        tasks = scheduler.get_next_tasks(max_tasks=2)
        assert len(tasks) == 2

    def test_mark_scheduled(self):
        state = DiscoveryState()
        h = Hypothesis(title="Test", source="test", target="/test", vulnerability_class="XSS")
        state.add_hypothesis(h)

        scheduler = ExperimentScheduler(state)
        exp_id = scheduler.mark_scheduled(h.id, "agent_1")
        assert exp_id is not None
        assert h.status == HypothesisStatus.in_progress
        assert len(state.experiments) == 1
        assert state.experiments[0].status == ExperimentStatus.running

    def test_has_pending_work(self):
        state = DiscoveryState(max_concurrent_experiments=2)
        scheduler = ExperimentScheduler(state)

        assert not scheduler.has_pending_work()

        h = Hypothesis(title="Test", source="test", target="/test", vulnerability_class="XSS")
        state.add_hypothesis(h)

        assert scheduler.has_pending_work()

    def test_schedule_summary(self):
        state = DiscoveryState(max_concurrent_experiments=3)
        h = Hypothesis(title="Test", source="test", target="/test", vulnerability_class="XSS")
        state.add_hypothesis(h)

        scheduler = ExperimentScheduler(state)
        summary = scheduler.get_schedule_summary()
        assert summary["queued_hypotheses"] == 1
        assert summary["running_experiments"] == 0
        assert summary["available_slots"] == 3
        assert summary["has_pending_work"]

    def test_skips_non_queued(self):
        state = DiscoveryState()
        h = Hypothesis(title="Done", source="test", target="/done", vulnerability_class="XSS")
        h.status = HypothesisStatus.validated
        state.hypotheses.append(h)

        scheduler = ExperimentScheduler(state)
        tasks = scheduler.get_next_tasks()
        assert len(tasks) == 0


class TestSuggestSkills:
    def test_idor_skills(self):
        skills = _suggest_skills("IDOR")
        assert "idor" in skills

    def test_injection_skills(self):
        skills = _suggest_skills("SQL Injection")
        assert "sql_injection" in skills

    def test_xss_skills(self):
        skills = _suggest_skills("XSS")
        assert "xss" in skills

    def test_unknown_class(self):
        skills = _suggest_skills("Something Unknown")
        assert len(skills) == 0

    def test_max_3_skills(self):
        skills = _suggest_skills("Authorization Bypass IDOR")
        assert len(skills) <= 3
