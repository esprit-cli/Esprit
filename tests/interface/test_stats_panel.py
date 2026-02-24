"""Tests for live TUI stats and cost display."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from esprit.interface.utils import build_live_stats_text, build_tui_stats_text


class _FakePricingDB:
    def get_context_limit(self, model: str) -> int:
        return 128_000

    def get_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int,
    ) -> float:
        return 0.12


def test_tui_stats_shows_billable_input_and_cache_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer = SimpleNamespace(
        agents={"agent_1": {}},
        tool_executions={},
        vulnerability_reports=[],
        start_time=datetime.now(timezone.utc).isoformat(),
        get_real_tool_count=lambda: 0,
        get_total_llm_stats=lambda: {
            "total": {
                "input_tokens": 1_000,
                "output_tokens": 200,
                "cached_tokens": 400,
                "cost": 0.12,
                "requests": 5,
            },
            "max_context_tokens": 800,
            "uncached_input_tokens": 600,
            "cache_hit_ratio": 40.0,
        },
    )
    agent_config = {
        "llm_config": SimpleNamespace(model_name="anthropic/claude-3-5-sonnet-20241022")
    }

    monkeypatch.setattr("esprit.llm.pricing.get_pricing_db", lambda: _FakePricingDB())
    monkeypatch.setattr("esprit.llm.pricing.get_lifetime_cost", lambda: 0.0)

    text = build_tui_stats_text(tracer, agent_config=agent_config, spinner_frame=0)
    plain = text.plain

    assert "▸ Bill " in plain
    assert "600" in plain
    assert "(40% hit)" in plain
    assert "$0.12" in plain


def test_tui_stats_uses_ascii_markers_for_vulns_and_tips(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer = SimpleNamespace(
        agents={"agent_1": {}},
        tool_executions={},
        vulnerability_reports=[{"severity": "high"}],
        start_time=datetime.now(timezone.utc).isoformat(),
        get_real_tool_count=lambda: 0,
        get_total_llm_stats=lambda: {
            "total": {
                "input_tokens": 10,
                "output_tokens": 5,
                "cached_tokens": 0,
                "cost": 0.12,
                "requests": 1,
            },
            "max_context_tokens": 10,
            "uncached_input_tokens": 10,
            "cache_hit_ratio": 0.0,
        },
    )
    agent_config = {"llm_config": SimpleNamespace(model_name="openai/gpt-5")}

    monkeypatch.setattr("esprit.llm.pricing.get_pricing_db", lambda: _FakePricingDB())
    monkeypatch.setattr("esprit.llm.pricing.get_lifetime_cost", lambda: 0.0)

    text = build_tui_stats_text(tracer, agent_config=agent_config, spinner_frame=0)
    plain = text.plain

    assert "[warn]" in plain
    assert "[run]" in plain
    for removed_marker in ("⚠", "💬", "🔄", "🔑", "📊", "🔀", "⌨️", "🔍", "💰", "📁", "👥"):
        assert removed_marker not in plain


def test_tui_stats_show_projection_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer = SimpleNamespace(
        agents={"agent_1": {"status": "running"}},
        tool_executions={},
        vulnerability_reports=[],
        start_time=datetime.now(timezone.utc).isoformat(),
        get_real_tool_count=lambda: 0,
        get_total_llm_stats=lambda: {
            "total": {
                "input_tokens": 2_000,
                "output_tokens": 500,
                "cached_tokens": 200,
                "requests": 8,
            },
            "max_context_tokens": 1_200,
            "uncached_input_tokens": 1_800,
            "cache_hit_ratio": 10.0,
        },
    )
    agent_config = {"llm_config": SimpleNamespace(model_name="openai/gpt-5")}

    monkeypatch.setattr("esprit.llm.pricing.get_pricing_db", lambda: _FakePricingDB())
    monkeypatch.setattr("esprit.llm.pricing.get_lifetime_cost", lambda: 0.0)

    text = build_tui_stats_text(tracer, agent_config=agent_config, spinner_frame=0)
    plain = text.plain

    assert "Proj " in plain
    assert "total " in plain


def test_tui_stats_prefers_tracer_aggregated_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer = SimpleNamespace(
        agents={"agent_1": {}},
        tool_executions={},
        vulnerability_reports=[],
        start_time=datetime.now(timezone.utc).isoformat(),
        get_real_tool_count=lambda: 0,
        get_total_llm_stats=lambda: {
            "total": {
                "input_tokens": 50_000,
                "output_tokens": 12_000,
                "cached_tokens": 10_000,
                "cost": 0.03,
                "requests": 12,
            },
            "max_context_tokens": 8_000,
            "uncached_input_tokens": 40_000,
            "cache_hit_ratio": 20.0,
        },
    )
    agent_config = {"llm_config": SimpleNamespace(model_name="openai/gpt-5")}

    monkeypatch.setattr("esprit.llm.pricing.get_pricing_db", lambda: _FakePricingDB())
    monkeypatch.setattr("esprit.llm.pricing.get_lifetime_cost", lambda: 0.0)

    text = build_tui_stats_text(tracer, agent_config=agent_config, spinner_frame=0)
    plain = text.plain

    assert "$0.03" in plain
    assert "$0.12" not in plain


def test_live_stats_prefers_tracer_aggregated_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer = SimpleNamespace(
        agents={"agent_1": {}},
        tool_executions={},
        vulnerability_reports=[],
        get_real_tool_count=lambda: 0,
        get_total_llm_stats=lambda: {
            "total": {
                "input_tokens": 90_000,
                "output_tokens": 15_000,
                "cached_tokens": 5_000,
                "cost": 0.07,
                "requests": 9,
            },
        },
    )
    agent_config = {"llm_config": SimpleNamespace(model_name="anthropic/claude-3-5-sonnet-20241022")}

    monkeypatch.setattr("esprit.llm.pricing.get_pricing_db", lambda: _FakePricingDB())

    text = build_live_stats_text(tracer, agent_config=agent_config)
    plain = text.plain

    assert "Cost $0.07" in plain
    assert "$0.12" not in plain
