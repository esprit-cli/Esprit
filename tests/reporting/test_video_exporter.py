from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from esprit.reporting.video_exporter import VideoExporter, MissingDependencyError


def make_tracer():
    tracer = MagicMock()
    tracer.run_id = "test-run-001"
    tracer.run_name = "test-scan"
    tracer.start_time = "2026-02-18T10:00:00+00:00"
    tracer.end_time = "2026-02-18T10:04:31+00:00"
    tracer.chat_messages = [
        {"timestamp": "2026-02-18T10:00:01+00:00", "role": "assistant", "content": "Starting scan"},
    ]
    tracer.tool_executions = {
        1: {
            "tool_name": "terminal",
            "agent_id": "agent-1",
            "started_at": "2026-02-18T10:00:02+00:00",
            "completed_at": "2026-02-18T10:00:03+00:00",
            "status": "success",
            "args": {"command": "nmap -sV target.com"},
            "result": "80/tcp open http",
        }
    }
    tracer.vulnerability_reports = [
        {
            "id": "vuln-001",
            "title": "SQL Injection",
            "severity": "critical",
            "cvss": 9.8,
            "timestamp": "2026-02-18T10:02:00+00:00",
            "target": "https://target.com",
            "endpoint": "/api/users",
        }
    ]
    tracer.agents = {
        "agent-1": {
            "name": "root-agent",
            "task": "Orchestrating scan",
            "status": "done",
            "created_at": "2026-02-18T10:00:00+00:00",
            "parent_id": None,
        }
    }
    tracer.scan_config = {
        "targets": [{"original": "target.com"}],
        "run_name": "test-scan",
    }
    tracer.run_metadata = {"status": "complete"}
    tracer._calculate_duration = MagicMock(return_value=271.0)
    return tracer


def test_build_events_includes_messages_tools_vulns():
    exporter = VideoExporter(make_tracer())
    events = exporter._build_events()
    types = [e["type"] for e in events]
    assert "message" in types
    assert "tool_start" in types
    assert "vulnerability" in types


def test_build_events_sorted_by_timestamp():
    exporter = VideoExporter(make_tracer())
    events = exporter._build_events()
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)


def test_render_template_produces_html(tmp_path):
    exporter = VideoExporter(make_tracer())
    events = exporter._build_events()
    html_path = exporter._render_template(events, speed=10.0, resolution=(1920, 1080), tmp_dir=tmp_path)
    assert html_path.exists()
    content = html_path.read_text()
    assert "ESPRIT_DONE" in content
    assert "ESPRIT_LOADING" in content
    assert "test-scan" in content
    assert 'id="final-logo"' not in content
    assert 'id="mini-terminal-lines"' in content
    assert 'id="mini-browser-actions"' in content


def test_render_template_escapes_script_breakers(tmp_path):
    tracer = make_tracer()
    tracer.chat_messages.append(
        {
            "timestamp": "2026-02-18T10:00:04+00:00",
            "role": "assistant",
            "content": "payload \"></script><script>alert(1)</script>",
        }
    )
    exporter = VideoExporter(tracer)
    events = exporter._build_events()
    html_path = exporter._render_template(events, speed=10.0, resolution=(1920, 1080), tmp_dir=tmp_path)
    content = html_path.read_text()
    assert "JSON.parse(atob(" in content
    assert "\"></script><script>alert(1)</script>" not in content


def test_render_template_embeds_speed(tmp_path):
    exporter = VideoExporter(make_tracer())
    events = exporter._build_events()
    html_path = exporter._render_template(events, speed=20.0, resolution=(1920, 1080), tmp_dir=tmp_path)
    content = html_path.read_text()
    assert "20" in content  # speed value embedded



def test_build_events_include_tool_end_summary():
    exporter = VideoExporter(make_tracer())
    events = exporter._build_events()
    tool_end = next(e for e in events if e["type"] == "tool_end")
    assert tool_end["data"]["execution_id"] == 1
    assert tool_end["data"]["tool_name"] == "terminal"
    assert "response_preview" in tool_end["data"]


def test_build_events_browser_screenshot_is_data_url():
    tracer = make_tracer()
    tracer.tool_executions[2] = {
        "tool_name": "browser_action",
        "agent_id": "agent-1",
        "started_at": "2026-02-18T10:00:04+00:00",
        "completed_at": "2026-02-18T10:00:05+00:00",
        "status": "success",
        "args": {"action": "goto", "url": "https://target.com"},
        "result": {
            "url": "https://target.com",
            "title": "Target",
            "viewport": {"width": 1280, "height": 720},
            "screenshot": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ",
            "message": "navigated",
        },
    }

    exporter = VideoExporter(tracer)
    events = exporter._build_events()
    browser_end = next(
        e for e in events
        if e["type"] == "tool_end" and e["data"].get("tool_name") == "browser_action"
    )

    shot = browser_end["data"].get("browser_screenshot")
    assert isinstance(shot, str)
    assert shot.startswith("data:image/png;base64,")

def test_missing_playwright_raises_clear_error(tmp_path):
    exporter = VideoExporter(make_tracer())
    with patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}):
        with pytest.raises(MissingDependencyError, match="playwright"):
            exporter._record_with_playwright(Path("/fake.html"), tmp_path, (1920, 1080))


def test_missing_ffmpeg_raises_clear_error(tmp_path):
    exporter = VideoExporter(make_tracer())
    fake_webm = tmp_path / "fake.webm"
    fake_webm.write_bytes(b"")
    with patch("shutil.which", return_value=None):
        with pytest.raises(MissingDependencyError, match="ffmpeg"):
            exporter._convert_to_mp4(fake_webm, tmp_path / "out.mp4")
