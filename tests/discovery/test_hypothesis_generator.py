"""Tests for hypothesis generation from anomalies."""

from esprit.discovery.models import (
    AnomalyEvent,
    AnomalyType,
    DiscoveryState,
    EvidenceRef,
    Hypothesis,
    HypothesisStatus,
)
from esprit.discovery.hypothesis_generator import HypothesisGenerator, _normalize_target


class TestHypothesisGeneration:
    def test_generate_from_status_code_403(self):
        state = DiscoveryState()
        gen = HypothesisGenerator(state)
        anomaly = AnomalyEvent(
            anomaly_type=AnomalyType.status_code,
            source_tool="proxy",
            description="HTTP 403 on GET /api/admin",
            target="GET /api/admin",
            raw_data={"status_code": 403, "method": "GET", "path": "/api/admin"},
            evidence_refs=[EvidenceRef(source="proxy", ref_id="req_1")],
        )
        hypotheses = gen.generate_from_anomaly(anomaly)
        assert len(hypotheses) >= 1
        assert hypotheses[0].vulnerability_class == "Authorization Bypass"
        assert hypotheses[0].impact_score > 0

    def test_generate_from_injection_signal(self):
        state = DiscoveryState()
        gen = HypothesisGenerator(state)
        anomaly = AnomalyEvent(
            anomaly_type=AnomalyType.injection_signal,
            source_tool="proxy",
            description="SQL syntax error in response",
            target="POST /api/search",
            raw_data={"pattern": "sql error"},
        )
        hypotheses = gen.generate_from_anomaly(anomaly)
        assert len(hypotheses) == 1
        assert hypotheses[0].vulnerability_class == "Injection"
        assert hypotheses[0].impact_score >= 0.8

    def test_generate_from_error_leak(self):
        state = DiscoveryState()
        gen = HypothesisGenerator(state)
        anomaly = AnomalyEvent(
            anomaly_type=AnomalyType.error_leak,
            source_tool="terminal",
            description="Stack trace in response",
            target="/api/debug",
        )
        hypotheses = gen.generate_from_anomaly(anomaly)
        assert len(hypotheses) == 1
        assert hypotheses[0].vulnerability_class == "Information Disclosure"

    def test_deduplicate_same_target_and_class(self):
        state = DiscoveryState()
        # Pre-existing hypothesis
        existing = Hypothesis(
            title="Existing",
            source="proxy_status_code",
            target="GET /api/admin",
            vulnerability_class="Authorization Bypass",
        )
        state.add_hypothesis(existing)

        gen = HypothesisGenerator(state)
        anomaly = AnomalyEvent(
            anomaly_type=AnomalyType.status_code,
            source_tool="proxy",
            description="HTTP 403 on GET /api/admin",
            target="GET /api/admin",
            raw_data={"status_code": 403},
        )
        hypotheses = gen.generate_from_anomaly(anomaly)
        assert len(hypotheses) == 0  # should be deduped

    def test_novelty_decreases_with_repeated_targets(self):
        state = DiscoveryState()
        gen = HypothesisGenerator(state)

        # First hypothesis has high novelty
        anomaly1 = AnomalyEvent(
            anomaly_type=AnomalyType.error_leak,
            source_tool="proxy",
            description="Error leak on /api/users",
            target="/api/users",
        )
        hyps1 = gen.generate_from_anomaly(anomaly1)
        assert hyps1[0].novelty_score == 0.9

        # Add it to state
        state.add_hypothesis(hyps1[0])

        # Second hypothesis on different target keeps high novelty
        anomaly2 = AnomalyEvent(
            anomaly_type=AnomalyType.injection_signal,
            source_tool="proxy",
            description="Injection on /api/orders",
            target="/api/orders",
        )
        hyps2 = gen.generate_from_anomaly(anomaly2)
        assert hyps2[0].novelty_score > 0.5

    def test_batch_generation_respects_limit(self):
        state = DiscoveryState(max_hypotheses_per_iteration=2)
        gen = HypothesisGenerator(state)
        anomalies = [
            AnomalyEvent(
                anomaly_type=AnomalyType.error_leak,
                source_tool="proxy",
                description=f"Error on /api/endpoint{i}",
                target=f"/api/endpoint{i}",
            )
            for i in range(5)
        ]
        hypotheses = gen.generate_from_anomalies(anomalies)
        assert len(hypotheses) <= 2

    def test_reachability_proxy_highest(self):
        state = DiscoveryState()
        gen = HypothesisGenerator(state)

        proxy_anomaly = AnomalyEvent(
            anomaly_type=AnomalyType.error_leak,
            source_tool="proxy",
            description="Error leak",
            target="/api/test",
        )
        terminal_anomaly = AnomalyEvent(
            anomaly_type=AnomalyType.error_leak,
            source_tool="terminal",
            description="Error leak",
            target="/api/test2",
        )

        hyps_proxy = gen.generate_from_anomaly(proxy_anomaly)
        state.add_hypothesis(hyps_proxy[0])
        hyps_terminal = gen.generate_from_anomaly(terminal_anomaly)

        assert hyps_proxy[0].reachability_score > hyps_terminal[0].reachability_score


class TestNormalizeTarget:
    def test_strip_method(self):
        assert _normalize_target("GET /api/users") == "/api/users"
        assert _normalize_target("POST /api/users") == "/api/users"

    def test_replace_numeric_ids(self):
        assert _normalize_target("/api/users/123") == "/api/users/{id}"
        assert _normalize_target("/api/users/123/posts/456") == "/api/users/{id}/posts/{id}"

    def test_replace_uuid_ids(self):
        result = _normalize_target("/api/users/550e8400-e29b-41d4-a716-446655440000")
        assert result == "/api/users/{id}"

    def test_case_insensitive(self):
        assert _normalize_target("GET /API/Users") == "/api/users"
