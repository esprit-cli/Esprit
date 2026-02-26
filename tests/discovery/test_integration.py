"""Tests for discovery engine integration with agent loop."""

from esprit.discovery.integration import DiscoveryIntegration
from esprit.discovery.models import (
    DiscoveryState,
    Hypothesis,
    HypothesisStatus,
)


class TestContextBlock:
    def test_no_context_when_disabled(self):
        integration = DiscoveryIntegration(enabled=False)
        assert integration.build_context_block() is None

    def test_no_context_when_empty(self):
        integration = DiscoveryIntegration()
        assert integration.build_context_block() is None

    def test_context_with_queued_hypotheses(self):
        state = DiscoveryState()
        h = Hypothesis(
            title="IDOR on /api/users",
            source="proxy",
            target="/api/users/{id}",
            vulnerability_class="IDOR",
            novelty_score=0.8,
        )
        state.add_hypothesis(h)

        integration = DiscoveryIntegration(state=state)
        block = integration.build_context_block()
        assert block is not None
        assert "<discovery_context>" in block
        assert "IDOR" in block
        assert h.id in block


class TestToolResultProcessing:
    def test_process_proxy_anomaly(self):
        integration = DiscoveryIntegration()
        result = {
            "requests": [
                {
                    "id": "req_1",
                    "method": "GET",
                    "host": "example.com",
                    "path": "/api/admin",
                    "response": {"statusCode": 403, "roundtripTime": 100},
                }
            ]
        }
        count = integration.process_tool_result("list_requests", {}, result)
        assert count >= 1
        assert len(integration.state.hypotheses) >= 1
        assert len(integration.state.anomaly_events) >= 1

    def test_no_processing_when_disabled(self):
        integration = DiscoveryIntegration(enabled=False)
        result = {
            "requests": [
                {
                    "id": "req_1",
                    "method": "GET",
                    "host": "example.com",
                    "path": "/api/admin",
                    "response": {"statusCode": 500, "roundtripTime": 100},
                }
            ]
        }
        count = integration.process_tool_result("list_requests", {}, result)
        assert count == 0

    def test_deduplicates_across_calls(self):
        integration = DiscoveryIntegration()
        result = {
            "requests": [
                {
                    "id": "req_1",
                    "method": "GET",
                    "host": "example.com",
                    "path": "/api/admin",
                    "response": {"statusCode": 403, "roundtripTime": 100},
                }
            ]
        }
        count1 = integration.process_tool_result("list_requests", {}, result)
        count2 = integration.process_tool_result("list_requests", {}, result)
        # Second call should produce 0 new hypotheses (deduped)
        assert count2 == 0


class TestFinishGuards:
    def test_no_untested_when_empty(self):
        integration = DiscoveryIntegration()
        assert not integration.has_untested_high_priority()

    def test_detects_untested_high_priority(self):
        state = DiscoveryState()
        h = Hypothesis(
            title="Critical finding",
            source="proxy",
            target="/api/admin",
            vulnerability_class="Auth Bypass",
            novelty_score=0.9,
            impact_score=0.9,
        )
        state.add_hypothesis(h)

        integration = DiscoveryIntegration(state=state)
        assert integration.has_untested_high_priority(threshold=0.3)

    def test_no_untested_after_validation(self):
        state = DiscoveryState()
        h = Hypothesis(
            title="Validated",
            source="proxy",
            target="/api/test",
            vulnerability_class="XSS",
        )
        h.status = HypothesisStatus.validated
        state.hypotheses.append(h)

        integration = DiscoveryIntegration(state=state)
        assert not integration.has_untested_high_priority()

    def test_untested_summary(self):
        state = DiscoveryState()
        h = Hypothesis(
            title="IDOR test",
            source="proxy",
            target="/api/users",
            vulnerability_class="IDOR",
            novelty_score=0.9,
            impact_score=0.8,
            evidence_score=0.7,
        )
        state.add_hypothesis(h)

        integration = DiscoveryIntegration(state=state)
        summary = integration.get_untested_summary(threshold=0.3)
        assert "IDOR" in summary


class TestExperimentLifecycle:
    def test_complete_experiment_validated(self):
        integration = DiscoveryIntegration()
        h = Hypothesis(
            title="Test",
            source="proxy",
            target="/test",
            vulnerability_class="XSS",
        )
        hid = integration.tracker.submit_hypothesis(h)
        eid = integration.tracker.start_experiment(hid, "agent_1", "Test task")

        integration.complete_experiment_from_agent_result(
            "agent_1",
            success=True,
            result_summary="Found XSS",
            findings=["Reflected XSS in search param"],
        )

        assert h.status == HypothesisStatus.validated

    def test_complete_experiment_falsified(self):
        integration = DiscoveryIntegration()
        h = Hypothesis(
            title="Test",
            source="proxy",
            target="/test",
            vulnerability_class="XSS",
        )
        hid = integration.tracker.submit_hypothesis(h)
        integration.tracker.start_experiment(hid, "agent_2", "Test task")

        integration.complete_experiment_from_agent_result(
            "agent_2",
            success=False,
            result_summary="Not vulnerable",
        )

        assert h.status == HypothesisStatus.falsified


class TestPersistence:
    def test_get_persistence_data(self):
        state = DiscoveryState()
        h = Hypothesis(title="Test", source="test", target="/test")
        state.add_hypothesis(h)

        integration = DiscoveryIntegration(state=state)
        data = integration.get_persistence_data()
        assert "hypotheses" in data
        assert "discovery_metrics" in data
        assert len(data["hypotheses"]) == 1
