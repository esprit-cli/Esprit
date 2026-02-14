"""Tests for tracer LLM token aggregation helpers."""

from types import SimpleNamespace

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
