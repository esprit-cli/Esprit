"""Tests for the TracerBridge module."""

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from esprit.telemetry.tracer import Tracer


def _make_tracer_with_data() -> Tracer:
    """Create a tracer with some test data populated."""
    tracer = Tracer("test-run")

    # Add agents
    tracer.log_agent_creation("agent-1", "Scanner", "Scan target", parent_id=None)
    tracer.log_agent_creation("agent-2", "Browser", "Browse site", parent_id="agent-1")

    # Add chat messages
    tracer.log_chat_message("Starting scan", "system", agent_id="agent-1")
    tracer.log_chat_message("I will analyze the target", "assistant", agent_id="agent-1")
    tracer.log_chat_message("Please focus on auth", "user")

    # Add tool executions
    exec_id1 = tracer.log_tool_execution_start("agent-1", "terminal", {"command": "nmap"})
    tracer.update_tool_execution(exec_id1, "completed", {"output": "scan results"})

    exec_id2 = tracer.log_tool_execution_start("agent-2", "browser_action", {"url": "https://example.com"})
    tracer.update_tool_execution(exec_id2, "completed", {
        "screenshot": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==",
        "url": "https://example.com/page",
    })
    tracer.latest_browser_screenshots["agent-2"] = exec_id2

    # Add vulnerability
    tracer.add_vulnerability_report(
        title="XSS in search",
        severity="high",
        description="Reflected XSS",
        target="https://example.com",
    )

    # Add streaming content
    tracer.update_streaming_content("agent-1", "I am thinking about...")

    return tracer


class TestTracerBridgeInit:
    """Tests for TracerBridge initialization."""

    def test_create_bridge(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)
        assert bridge._tracer is tracer
        assert bridge._clients == []
        assert bridge._last_agent_count == 0
        assert bridge._last_tool_count == 0
        assert bridge._last_chat_count == 0
        assert bridge._last_vuln_count == 0


class TestTracerBridgeClientManagement:
    """Tests for WebSocket client management."""

    def test_add_client(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)
        mock_ws = MagicMock()

        bridge.add_client(mock_ws)
        assert mock_ws in bridge._clients

    def test_add_client_no_duplicates(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)
        mock_ws = MagicMock()

        bridge.add_client(mock_ws)
        bridge.add_client(mock_ws)
        assert len(bridge._clients) == 1

    def test_remove_client(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)
        mock_ws = MagicMock()

        bridge.add_client(mock_ws)
        bridge.remove_client(mock_ws)
        assert mock_ws not in bridge._clients

    def test_remove_nonexistent_client(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)
        mock_ws = MagicMock()

        # Should not raise
        bridge.remove_client(mock_ws)


class TestTracerBridgeFullState:
    """Tests for full state snapshot generation."""

    def test_full_state_empty_tracer(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)
        state = bridge.get_full_state()

        assert state["type"] == "full_state"
        assert state["agents"] == []
        assert state["tools"] == []
        assert state["chat"] == []
        assert state["vulnerabilities"] == []
        assert state["streaming"] == {}
        assert state["screenshot_agents"] == []
        assert "stats" in state
        assert "timestamp" in state

    def test_full_state_with_data(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = _make_tracer_with_data()
        bridge = TracerBridge(tracer)
        state = bridge.get_full_state()

        assert state["type"] == "full_state"
        assert len(state["agents"]) == 2
        assert len(state["chat"]) >= 3
        assert len(state["vulnerabilities"]) == 1
        assert "agent-1" in state["streaming"]
        assert "agent-2" in state["screenshot_agents"]

    def test_full_state_agent_serialization(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = _make_tracer_with_data()
        bridge = TracerBridge(tracer)
        state = bridge.get_full_state()

        agents = state["agents"]
        scanner = next(a for a in agents if a["id"] == "agent-1")
        assert scanner["name"] == "Scanner"
        assert scanner["task"] == "Scan target"
        assert scanner["parent_id"] is None
        assert scanner["has_screenshot"] is False

        browser = next(a for a in agents if a["id"] == "agent-2")
        assert browser["name"] == "Browser"
        assert browser["parent_id"] == "agent-1"
        assert browser["has_screenshot"] is True

    def test_full_state_stats(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = _make_tracer_with_data()
        bridge = TracerBridge(tracer)
        state = bridge.get_full_state()

        stats = state["stats"]
        assert stats["agent_count"] == 2
        assert stats["vuln_count"] == 1
        assert stats["start_time"] == tracer.start_time
        assert stats["status"] == "running"


class TestTracerBridgeScreenshot:
    """Tests for screenshot REST endpoint data."""

    def test_get_screenshot_with_tracked_latest(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = _make_tracer_with_data()
        bridge = TracerBridge(tracer)
        data = bridge.get_screenshot("agent-2")

        assert data["agent_id"] == "agent-2"
        assert data["screenshot"] is not None
        assert len(data["screenshot"]) > 0
        assert data["url"] == "https://example.com/page"

    def test_get_screenshot_no_screenshot(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = _make_tracer_with_data()
        bridge = TracerBridge(tracer)
        data = bridge.get_screenshot("agent-1")

        assert data["screenshot"] is None
        assert data["agent_id"] == "agent-1"

    def test_get_screenshot_nonexistent_agent(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = _make_tracer_with_data()
        bridge = TracerBridge(tracer)
        data = bridge.get_screenshot("nonexistent")

        assert data["screenshot"] is None
        assert data["agent_id"] == "nonexistent"

    def test_get_screenshot_rendered_placeholder_skipped(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        tracer.log_agent_creation("agent-x", "Test", "test")
        exec_id = tracer.log_tool_execution_start("agent-x", "browser_action", {"url": "http://test"})
        tracer.update_tool_execution(exec_id, "completed", {
            "screenshot": "[rendered]",
            "url": "http://test",
        })
        tracer.latest_browser_screenshots["agent-x"] = exec_id

        bridge = TracerBridge(tracer)
        data = bridge.get_screenshot("agent-x")
        assert data["screenshot"] is None


class TestTracerBridgeDeltaDetection:
    """Tests for delta detection logic."""

    def test_no_deltas_when_unchanged(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = _make_tracer_with_data()
        bridge = TracerBridge(tracer)

        # First call detects everything as new
        deltas1 = bridge._detect_deltas()
        assert len(deltas1) > 0

        # Second call should detect no changes
        deltas2 = bridge._detect_deltas()
        assert len(deltas2) == 0

    def test_new_agent_detected(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)

        # Initial — no agents
        bridge._detect_deltas()

        # Add an agent
        tracer.log_agent_creation("new-agent", "New", "task")
        deltas = bridge._detect_deltas()
        agent_updates = [d for d in deltas if d["type"] == "agents_update"]
        assert len(agent_updates) == 1

    def test_new_chat_message_detected(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)

        # Initial
        bridge._detect_deltas()

        # Add message
        tracer.log_chat_message("Hello", "user")
        deltas = bridge._detect_deltas()
        chat_updates = [d for d in deltas if d["type"] == "chat_update"]
        assert len(chat_updates) == 1
        assert chat_updates[0]["messages"][0]["content"] == "Hello"

    def test_new_vulnerability_detected(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)

        bridge._detect_deltas()

        tracer.add_vulnerability_report(
            title="SQL Injection",
            severity="critical",
            description="Found SQLi",
        )

        deltas = bridge._detect_deltas()
        vuln_updates = [d for d in deltas if d["type"] == "vulnerability_update"]
        assert len(vuln_updates) == 1
        assert vuln_updates[0]["vulnerabilities"][0]["title"] == "SQL Injection"

    def test_streaming_content_change_detected(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        tracer.log_agent_creation("a1", "Agent", "task")
        bridge = TracerBridge(tracer)

        bridge._detect_deltas()

        tracer.update_streaming_content("a1", "Thinking...")
        deltas = bridge._detect_deltas()
        streaming_updates = [d for d in deltas if d["type"] == "streaming_update"]
        assert len(streaming_updates) == 1
        assert streaming_updates[0]["streaming"]["a1"] == "Thinking..."

    def test_screenshot_change_detected(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        tracer.log_agent_creation("a1", "Agent", "task")
        bridge = TracerBridge(tracer)

        bridge._detect_deltas()

        tracer.latest_browser_screenshots["a1"] = 42
        deltas = bridge._detect_deltas()
        screenshot_updates = [d for d in deltas if d["type"] == "screenshot_update"]
        assert len(screenshot_updates) == 1
        assert screenshot_updates[0]["agent_id"] == "a1"

    def test_agent_status_change_detected(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        tracer.log_agent_creation("a1", "Agent", "task")
        bridge = TracerBridge(tracer)

        bridge._detect_deltas()

        tracer.update_agent_status("a1", "completed")
        deltas = bridge._detect_deltas()
        agent_updates = [d for d in deltas if d["type"] == "agents_update"]
        assert len(agent_updates) == 1


class TestTracerBridgeToolSerialization:
    """Tests for tool serialization with screenshot stripping."""

    def test_screenshots_stripped_from_tools(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = _make_tracer_with_data()
        bridge = TracerBridge(tracer)
        state = bridge.get_full_state()

        tools = state["tools"]
        browser_tools = [t for t in tools if t["tool_name"] == "browser_action"]
        assert len(browser_tools) == 1
        # Screenshot should not be in the result_summary
        assert "screenshot" not in browser_tools[0].get("result_summary", {})
        # But has_screenshot should be True
        assert browser_tools[0]["has_screenshot"] is True

    def test_tool_args_serialized(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = _make_tracer_with_data()
        bridge = TracerBridge(tracer)
        state = bridge.get_full_state()

        tools = state["tools"]
        terminal_tool = next(t for t in tools if t["tool_name"] == "terminal")
        assert terminal_tool["args"]["command"] == "nmap"
        assert terminal_tool["status"] == "completed"


class TestTracerBridgeBroadcast:
    """Tests for broadcasting to WebSocket clients."""

    def test_broadcast_sends_to_all_clients(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)

        ws1 = AsyncMock()
        ws2 = AsyncMock()
        bridge.add_client(ws1)
        bridge.add_client(ws2)

        messages = [{"type": "test_message"}]
        asyncio.get_event_loop().run_until_complete(bridge._broadcast(messages))

        ws1.send_text.assert_called_once()
        ws2.send_text.assert_called_once()

        # Verify payload format
        payload = json.loads(ws1.send_text.call_args[0][0])
        assert payload["type"] == "delta_batch"
        assert payload["deltas"] == messages

    def test_broadcast_removes_dead_clients(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

        tracer = Tracer("test")
        bridge = TracerBridge(tracer)

        ws_alive = AsyncMock()
        ws_dead = AsyncMock()
        ws_dead.send_text.side_effect = Exception("Connection closed")

        bridge.add_client(ws_alive)
        bridge.add_client(ws_dead)

        asyncio.get_event_loop().run_until_complete(bridge._broadcast([{"type": "test"}]))

        assert ws_alive in bridge._clients
        assert ws_dead not in bridge._clients
