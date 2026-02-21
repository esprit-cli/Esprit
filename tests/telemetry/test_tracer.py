"""Tests for tracer heartbeat and LLM token aggregation helpers."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from esprit.llm.llm import RequestStats
from esprit.telemetry.tracer import Tracer


class TestTracerHeartbeat:
    def test_touch_agent_heartbeat_stores_values_under_agent(self) -> None:
        tracer = Tracer("test-run")
        tracer.log_agent_creation("agent_1", "Agent 1", "Test task")

        tracer.touch_agent_heartbeat("agent_1", phase="before_llm_processing", detail="iter-3")

        agent_data = tracer.agents["agent_1"]
        heartbeat = agent_data.get("heartbeat")
        assert isinstance(heartbeat, dict)
        assert heartbeat["phase"] == "before_llm_processing"
        assert heartbeat["detail"] == "iter-3"
        assert isinstance(heartbeat["timestamp"], str)

        parsed = datetime.fromisoformat(heartbeat["timestamp"].replace("Z", "+00:00"))
        assert parsed.tzinfo == UTC

    def test_get_agent_heartbeat_returns_dict_or_none(self) -> None:
        tracer = Tracer("test-run")
        tracer.log_agent_creation("agent_1", "Agent 1", "Test task")

        assert tracer.get_agent_heartbeat("missing-agent") is None
        assert tracer.get_agent_heartbeat("agent_1") is None

        tracer.touch_agent_heartbeat("agent_1", phase="waiting_for_input")

        heartbeat = tracer.get_agent_heartbeat("agent_1")
        assert heartbeat is not None
        assert heartbeat["phase"] == "waiting_for_input"
        assert heartbeat["detail"] is None


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

class TestTracerRunMetadataPersistence:
    def test_mark_complete_sets_run_metadata_end_time_and_status(self, tmp_path: Path) -> None:
        tracer = Tracer("test-run")

        tracer.run_metadata["status"] = "running"
        tracer.run_metadata["end_time"] = None
        tracer._run_dir = tmp_path / "run"
        tracer._run_dir.mkdir(parents=True, exist_ok=True)

        tracer.save_run_data(mark_complete=True)

        assert tracer.run_metadata["status"] == "completed"
        assert isinstance(tracer.run_metadata["end_time"], str)

    def test_get_run_dir_creates_default_run_log_file(self, tmp_path: Path) -> None:
        tracer = Tracer("test-run")
        tracer._run_dir = tmp_path / "run"
        tracer._run_dir.mkdir(parents=True, exist_ok=True)

        run_dir = tracer.get_run_dir()

        assert (run_dir / "run.log").exists()


class TestTracerCheckpointSnapshot:
    def test_build_checkpoint_data_isolated_from_live_state(self) -> None:
        tracer = Tracer("test-run")
        tracer.log_agent_creation("agent_1", "Agent 1", "task")
        tracer.log_chat_message("hello", "assistant", "agent_1")
        tracer.log_tool_execution_start("agent_1", "tool_a", {"x": 1})
        tracer.vulnerability_reports.append(
            {
                "id": "vuln-0001",
                "title": "Test",
                "severity": "low",
                "timestamp": "2026-02-20 15:00:00 UTC",
            }
        )

        checkpoint = tracer._build_checkpoint_data()

        tracer.agents["agent_1"]["status"] = "completed"
        tracer.chat_messages[0]["content"] = "mutated"
        tracer.tool_executions[1]["status"] = "failed"
        tracer.vulnerability_reports[0]["title"] = "mutated"

        assert checkpoint["agents"]["agent_1"]["status"] == "running"
        assert checkpoint["chat_messages"][0]["content"] == "hello"
        assert checkpoint["tool_executions"][1]["status"] == "running"
        assert checkpoint["vulnerability_reports"][0]["title"] == "Test"

    def test_save_checkpoint_skips_failing_agent_state_dump(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        class _FailingState:
            def model_dump(self) -> dict:
                raise RuntimeError("mutable state race")

        failing_agent = SimpleNamespace(state=_FailingState())
        monkeypatch.setattr(
            "esprit.tools.agents_graph.agents_graph_actions._agent_instances",
            {"agent_fail": failing_agent},
            raising=False,
        )

        tracer = Tracer("test-run")
        checkpoint_path = tmp_path / "checkpoint.json"
        tracer.save_checkpoint(checkpoint_path)

        assert checkpoint_path.exists()
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        assert data["run_id"] == "test-run"
        assert data["agent_states"] == {}
