"""Tests for tracer LLM token aggregation helpers."""

import json
from types import SimpleNamespace
from typing import Any

import pytest

from esprit.llm.llm import RequestStats
from esprit.telemetry.tracer import Tracer


def _fake_agent(model_name: str, stats: RequestStats) -> SimpleNamespace:
    llm = SimpleNamespace(
        _total_stats=stats,
        config=SimpleNamespace(model_name=model_name),
    )
    return SimpleNamespace(llm=llm)


class TestTracerLLMStats:
    def test_includes_cache_metrics_and_breakdowns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake_instances = {
            "agent_a": _fake_agent(
                "anthropic/claude-3-5-sonnet-20241022",
                RequestStats(
                    input_tokens=1_000,
                    output_tokens=200,
                    cached_tokens=300,
                    cost=0.12,
                    requests=5,
                    last_input_tokens=700,
                ),
            ),
            "agent_b": _fake_agent(
                "openai/gpt-5",
                RequestStats(
                    input_tokens=500,
                    output_tokens=100,
                    cached_tokens=100,
                    cost=0.08,
                    requests=2,
                    last_input_tokens=350,
                ),
            ),
        }

        monkeypatch.setattr(
            "esprit.tools.agents_graph.agents_graph_actions._agent_instances",
            fake_instances,
            raising=False,
        )

        tracer = Tracer("test-run")
        stats = tracer.get_total_llm_stats()

        total = stats["total"]
        assert total["input_tokens"] == 1_500
        assert total["output_tokens"] == 300
        assert total["cached_tokens"] == 400
        assert total["uncached_input_tokens"] == 1_100
        assert total["cache_hit_ratio"] == 26.67
        assert total["requests"] == 7

        assert stats["max_context_tokens"] == 700
        assert stats["total_tokens"] == 1_800
        assert stats["uncached_input_tokens"] == 1_100
        assert stats["cache_hit_ratio"] == 26.67

        by_model = stats["by_model"]
        assert by_model["anthropic/claude-3-5-sonnet-20241022"]["cache_hit_ratio"] == 30.0
        assert by_model["openai/gpt-5"]["uncached_input_tokens"] == 400

        by_agent = stats["by_agent"]
        assert by_agent["agent_a"]["model"] == "anthropic/claude-3-5-sonnet-20241022"
        assert by_agent["agent_b"]["cache_hit_ratio"] == 20.0

    def test_handles_empty_agent_pool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "esprit.tools.agents_graph.agents_graph_actions._agent_instances",
            {},
            raising=False,
        )

        tracer = Tracer("test-run")
        stats = tracer.get_total_llm_stats()

        assert stats["total"]["input_tokens"] == 0
        assert stats["total"]["cache_hit_ratio"] == 0.0
        assert stats["total"]["uncached_input_tokens"] == 0
        assert stats["by_model"] == {}
        assert stats["by_agent"] == {}


class TestTracerRunStatus:
    def test_root_agent_failure_updates_run_status(self) -> None:
        tracer = Tracer("test-run")
        tracer.log_agent_creation("root", "Root", "task", parent_id=None)

        tracer.update_agent_status("root", "llm_failed", "provider error")

        assert tracer.run_metadata["status"] == "failed"

    def test_final_report_marks_completed_status_and_end_time(self) -> None:
        tracer = Tracer("test-run")
        tracer.update_scan_final_fields(
            executive_summary="done",
            methodology="m",
            technical_analysis="t",
            recommendations="r",
        )

        assert tracer.run_metadata["status"] == "completed"
        assert tracer.end_time is not None
        assert tracer.run_metadata["end_time"] == tracer.end_time


class TestTracerDiscoveryPersistence:
    def test_persists_discovery_files(self, tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        tracer = Tracer("discovery-run")
        tracer.set_discovery_state(
            "agent_root",
            {
                "hypotheses": [{"id": "hyp_1"}],
                "experiments": [{"id": "exp_1"}],
                "anomaly_events": [{"id": "anom_1"}],
                "evidence_index": {"proxy:req_1": {"source": "proxy", "ref_id": "req_1"}},
                "discovery_metrics": {
                    "validated_hypotheses": 1,
                    "completed_experiments": 1,
                },
            },
            is_root=True,
        )
        tracer.append_discovery_event("agent_root", {"type": "tool_result", "tool_name": "list_requests"})

        tracer.save_run_data()

        run_dir = tmp_path / "esprit_runs" / "discovery-run"
        assert (run_dir / "hypotheses.json").exists()
        assert (run_dir / "experiments.json").exists()
        assert (run_dir / "anomalies.json").exists()
        assert (run_dir / "evidence_index.json").exists()
        assert (run_dir / "discovery_metrics.json").exists()
        assert (run_dir / "discovery_events.json").exists()

        metrics = json.loads((run_dir / "discovery_metrics.json").read_text(encoding="utf-8"))
        assert metrics["validated_finding_rate"] == 1.0
