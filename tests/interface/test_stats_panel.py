"""Tests for live TUI stats token display."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from esprit.interface.utils import build_tui_stats_text


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
