"""Tests for live TUI stats token display."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from esprit.interface.launchpad import LaunchpadApp
from esprit.interface.utils import build_tui_stats_text, infer_scan_state


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
        streaming_content={},
        streaming_thinking={},
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


def test_launchpad_model_entries_do_not_duplicate_current_model_indicator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("esprit.interface.launchpad.Config.get", lambda _name: "anthropic/model-a")

    monkeypatch.setattr(
        "esprit.interface.launchpad.AVAILABLE_MODELS",
        {
            "anthropic": [
                ("model-a", "Model A", 1.0, 2.0, 200_000),
            ]
        },
    )

    app = LaunchpadApp()
    entries = app._build_model_entries()

    model_entries = [entry for entry in entries if entry.key == "model:anthropic/model-a"]
    assert len(model_entries) == 1
    assert model_entries[0].hint.count("★ active") == 1


def test_tui_stats_shows_scan_mode_and_estimate(monkeypatch: pytest.MonkeyPatch) -> None:
    tracer = SimpleNamespace(
        agents={},
        tool_executions={},
        vulnerability_reports=[],
        start_time=datetime.now(timezone.utc).isoformat(),
        scan_config={
            "scan_mode": "quick",
            "estimated_cost_low": 0.1,
            "estimated_cost_high": 0.4,
            "estimated_time_low_min": 6.0,
            "estimated_time_high_min": 14.0,
        },
        get_real_tool_count=lambda: 0,
        streaming_content={},
        streaming_thinking={},
        get_total_llm_stats=lambda: {
            "total": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "requests": 0,
            },
            "max_context_tokens": 0,
            "uncached_input_tokens": 0,
            "cache_hit_ratio": 0.0,
        },
    )
    agent_config = {
        "llm_config": SimpleNamespace(
            model_name="anthropic/claude-haiku-4-5-20251001",
            scan_mode="quick",
        )
    }

    monkeypatch.setattr("esprit.llm.pricing.get_pricing_db", lambda: _FakePricingDB())
    monkeypatch.setattr("esprit.llm.pricing.get_lifetime_cost", lambda: 0.0)

    text = build_tui_stats_text(tracer, agent_config=agent_config, spinner_frame=0)
    plain = text.plain

    assert "Mode Quick" in plain
    assert "$0.10-$0.40" in plain
    assert "~6-14m" in plain


def test_infer_scan_state_from_run_metadata_completed() -> None:
    tracer = SimpleNamespace(run_metadata={"status": "completed"}, agents={})
    done, failed = infer_scan_state(tracer)
    assert done is True
    assert failed is False


def test_build_tui_stats_treats_completed_run_metadata_as_done(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now_iso = datetime.now(timezone.utc).isoformat()
    tracer = SimpleNamespace(
        agents={"agent_1": {"status": "completed"}},
        tool_executions={},
        vulnerability_reports=[],
        start_time=now_iso,
        end_time=now_iso,
        run_metadata={"status": "completed", "end_time": now_iso},
        scan_config={"scan_mode": "deep"},
        get_real_tool_count=lambda: 0,
        streaming_content={},
        streaming_thinking={},
        get_total_llm_stats=lambda: {
            "total": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "requests": 0,
            },
            "max_context_tokens": 0,
            "uncached_input_tokens": 0,
            "cache_hit_ratio": 0.0,
        },
    )
    agent_config = {"llm_config": SimpleNamespace(model_name="anthropic/claude-haiku-4-5-20251001")}

    monkeypatch.setattr("esprit.llm.pricing.get_pricing_db", lambda: _FakePricingDB())
    monkeypatch.setattr("esprit.llm.pricing.get_lifetime_cost", lambda: 0.0)

    text = build_tui_stats_text(tracer, agent_config=agent_config, scan_completed=False, scan_failed=False)
    assert "Completed" in text.plain


def test_build_tui_stats_shows_saved_artifacts_after_completion(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "penetration_test_report.md").write_text("report", encoding="utf-8")
    (tmp_path / "vulnerabilities.csv").write_text("id,title\n", encoding="utf-8")
    (tmp_path / "replay.mp4").write_text("fake", encoding="utf-8")

    now_iso = datetime.now(timezone.utc).isoformat()
    tracer = SimpleNamespace(
        agents={"agent_1": {"status": "completed"}},
        tool_executions={},
        vulnerability_reports=[],
        start_time=now_iso,
        end_time=now_iso,
        run_metadata={"status": "completed", "end_time": now_iso},
        scan_config={"scan_mode": "deep"},
        _run_dir=tmp_path,
        get_real_tool_count=lambda: 0,
        streaming_content={},
        streaming_thinking={},
        get_total_llm_stats=lambda: {
            "total": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cached_tokens": 0,
                "requests": 0,
            },
            "max_context_tokens": 0,
            "uncached_input_tokens": 0,
            "cache_hit_ratio": 0.0,
        },
    )
    agent_config = {"llm_config": SimpleNamespace(model_name="anthropic/claude-haiku-4-5-20251001")}

    monkeypatch.setattr("esprit.llm.pricing.get_pricing_db", lambda: _FakePricingDB())
    monkeypatch.setattr("esprit.llm.pricing.get_lifetime_cost", lambda: 0.0)

    text = build_tui_stats_text(tracer, agent_config=agent_config)
    plain = text.plain
    assert "Output" in plain
    assert "penetration_test_report.md" in plain
    assert "vulnerabilities/ + vulnerabilities.csv" in plain
    assert "replay.mp4" in plain
