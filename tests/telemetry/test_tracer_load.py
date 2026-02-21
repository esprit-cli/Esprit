"""Tests for Tracer.load_from_dir classmethod."""

import json
from pathlib import Path

from esprit.telemetry.tracer import Tracer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_checkpoint(directory: Path, overrides: dict | None = None) -> dict:
    """Write a minimal valid checkpoint.json and return the data dict."""
    data: dict = {
        "run_id": "test-abc123",
        "run_name": "test-scan",
        "start_time": "2026-02-18T10:00:00+00:00",
        "end_time": "2026-02-18T10:04:31+00:00",
        "agents": {},
        "tool_executions": {},
        "chat_messages": [],
        "vulnerability_reports": [],
        "scan_results": None,
        "scan_config": {"targets": [{"original": "example.com"}]},
        "run_metadata": {
            "run_id": "test-abc123",
            "run_name": "test-scan",
            "start_time": "2026-02-18T10:00:00+00:00",
            "end_time": "2026-02-18T10:04:31+00:00",
            "targets": [],
            "status": "complete",
        },
        "next_execution_id": 1,
        "next_message_id": 1,
        "agent_states": {},
    }
    if overrides:
        data.update(overrides)
    (directory / "checkpoint.json").write_text(json.dumps(data), encoding="utf-8")
    return data


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestLoadFromDir:
    def test_returns_none_when_directory_is_empty(self, tmp_path: Path) -> None:
        """No checkpoint.json → should return None, not raise."""
        result = Tracer.load_from_dir(tmp_path)
        assert result is None

    def test_returns_none_when_checkpoint_missing(self, tmp_path: Path) -> None:
        """An unrelated file present but no checkpoint.json → still None."""
        (tmp_path / "some_other_file.txt").write_text("irrelevant")
        result = Tracer.load_from_dir(tmp_path)
        assert result is None

    def test_returns_tracer_with_correct_run_id(self, tmp_path: Path) -> None:
        _write_checkpoint(tmp_path)
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert tracer.run_id == "test-abc123"

    def test_returns_tracer_with_correct_run_name(self, tmp_path: Path) -> None:
        _write_checkpoint(tmp_path)
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert tracer.run_name == "test-scan"

    def test_preserves_start_and_end_time(self, tmp_path: Path) -> None:
        _write_checkpoint(tmp_path)
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert tracer.start_time == "2026-02-18T10:00:00+00:00"
        assert tracer.end_time == "2026-02-18T10:04:31+00:00"

    def test_scan_config_is_restored(self, tmp_path: Path) -> None:
        _write_checkpoint(tmp_path)
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert tracer.scan_config == {"targets": [{"original": "example.com"}]}

    def test_run_metadata_is_restored(self, tmp_path: Path) -> None:
        _write_checkpoint(tmp_path)
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert tracer.run_metadata["status"] == "complete"

    def test_run_dir_is_set_to_provided_path(self, tmp_path: Path) -> None:
        _write_checkpoint(tmp_path)
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert tracer._run_dir == tmp_path

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        """Callers may pass a plain string rather than a Path object."""
        _write_checkpoint(tmp_path)
        tracer = Tracer.load_from_dir(str(tmp_path))
        assert tracer is not None
        assert tracer.run_id == "test-abc123"

    def test_tool_executions_keys_are_integers(self, tmp_path: Path) -> None:
        """JSON serialises int keys as strings; they must be converted back."""
        _write_checkpoint(
            tmp_path,
            overrides={
                "tool_executions": {
                    "1": {"execution_id": 1, "tool_name": "nmap", "status": "completed"},
                    "2": {"execution_id": 2, "tool_name": "curl", "status": "completed"},
                },
                "next_execution_id": 3,
            },
        )
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert all(isinstance(k, int) for k in tracer.tool_executions)
        assert tracer.tool_executions[1]["tool_name"] == "nmap"
        assert tracer.tool_executions[2]["tool_name"] == "curl"

    def test_vulnerability_reports_are_restored(self, tmp_path: Path) -> None:
        vulns = [
            {"id": "vuln-0001", "title": "SQL Injection", "severity": "critical",
             "timestamp": "2026-02-18 10:01:00 UTC"},
        ]
        _write_checkpoint(tmp_path, overrides={"vulnerability_reports": vulns})
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert len(tracer.vulnerability_reports) == 1
        assert tracer.vulnerability_reports[0]["id"] == "vuln-0001"

    def test_chat_messages_are_restored(self, tmp_path: Path) -> None:
        messages = [
            {"message_id": 1, "content": "Hello", "role": "user", "agent_id": None,
             "timestamp": "2026-02-18T10:00:01+00:00", "metadata": {}},
        ]
        _write_checkpoint(tmp_path, overrides={"chat_messages": messages})
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert len(tracer.chat_messages) == 1
        assert tracer.chat_messages[0]["content"] == "Hello"

    def test_returns_none_on_corrupt_json(self, tmp_path: Path) -> None:
        """Corrupt checkpoint should not raise — just return None."""
        (tmp_path / "checkpoint.json").write_text("{ not valid json !!!", encoding="utf-8")
        result = Tracer.load_from_dir(tmp_path)
        assert result is None

    def test_scan_results_are_restored(self, tmp_path: Path) -> None:
        scan_results = {
            "scan_completed": True,
            "executive_summary": "No critical findings.",
            "success": True,
        }
        _write_checkpoint(tmp_path, overrides={"scan_results": scan_results})
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert tracer.scan_results is not None
        assert tracer.scan_results["scan_completed"] is True

    def test_none_end_time_is_preserved(self, tmp_path: Path) -> None:
        """A run that never finished should have end_time == None."""
        _write_checkpoint(tmp_path, overrides={"end_time": None})
        tracer = Tracer.load_from_dir(tmp_path)
        assert tracer is not None
        assert tracer.end_time is None
