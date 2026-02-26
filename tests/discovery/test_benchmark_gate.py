"""Tests for discovery benchmark CI gate."""

import json
from pathlib import Path

from esprit.discovery.benchmark_gate import (
    BenchmarkThresholds,
    evaluate_metrics,
    load_metrics,
    main,
)


class TestEvaluateMetrics:
    def test_passes_when_metrics_meet_thresholds(self) -> None:
        failures = evaluate_metrics(
            {
                "hypothesis_conversion_rate": 0.32,
                "novelty_ratio": 0.78,
                "validated_finding_rate": 0.22,
                "validated_hypotheses": 3,
            },
            BenchmarkThresholds(),
        )
        assert failures == []

    def test_returns_failures_when_below_thresholds(self) -> None:
        failures = evaluate_metrics(
            {
                "hypothesis_conversion_rate": 0.01,
                "novelty_ratio": 0.20,
                "validated_finding_rate": 0.0,
                "validated_hypotheses": 0,
            },
            BenchmarkThresholds(),
        )
        assert len(failures) == 4


class TestIoAndMain:
    def test_load_metrics_reads_json_object(self, tmp_path: Path) -> None:
        path = tmp_path / "metrics.json"
        path.write_text(json.dumps({"novelty_ratio": 0.8}), encoding="utf-8")
        metrics = load_metrics(path)
        assert metrics["novelty_ratio"] == 0.8

    def test_main_returns_zero_for_passing_metrics(self, tmp_path: Path) -> None:
        path = tmp_path / "metrics.json"
        path.write_text(
            json.dumps(
                {
                    "hypothesis_conversion_rate": 0.30,
                    "novelty_ratio": 0.80,
                    "validated_finding_rate": 0.25,
                    "validated_hypotheses": 2,
                }
            ),
            encoding="utf-8",
        )
        code = main(["--metrics", str(path)])
        assert code == 0

    def test_main_returns_non_zero_for_failing_metrics(self, tmp_path: Path) -> None:
        path = tmp_path / "metrics.json"
        path.write_text(
            json.dumps(
                {
                    "hypothesis_conversion_rate": 0.0,
                    "novelty_ratio": 0.1,
                    "validated_finding_rate": 0.0,
                    "validated_hypotheses": 0,
                }
            ),
            encoding="utf-8",
        )
        code = main(["--metrics", str(path)])
        assert code == 1
