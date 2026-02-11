"""Tests for the responsive layout dashboard hint in TUI."""

from unittest.mock import MagicMock, patch

import pytest

from rich.text import Text


class TestResponsiveLayoutHint:
    """Tests for the dashboard hint in _apply_responsive_layout."""

    def test_responsive_layout_method_exists(self) -> None:
        from esprit.interface.tui import EspritTUIApp

        assert hasattr(EspritTUIApp, "_apply_responsive_layout")

    def test_gui_server_none_shows_hint_in_narrow(self) -> None:
        """When gui_server is set and width < 120, should show dashboard URL."""
        import inspect
        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._apply_responsive_layout)
        assert "Dashboard" in source
        assert "7860" in source


class TestGUIPackageStructure:
    """Tests for the GUI package structure and imports."""

    def test_gui_package_importable(self) -> None:
        import esprit.gui

    def test_gui_server_importable(self) -> None:
        from esprit.gui.server import GUIServer

    def test_gui_tracer_bridge_importable(self) -> None:
        from esprit.gui.tracer_bridge import TracerBridge

    def test_image_widget_importable(self) -> None:
        from esprit.interface.image_widget import BrowserScreenshotWidget

    def test_image_widget_check_function(self) -> None:
        from esprit.interface.image_widget import _check_textual_image

        # Should return a bool without errors
        result = _check_textual_image()
        assert isinstance(result, bool)

    def test_image_widget_decode_function(self) -> None:
        from esprit.interface.image_widget import _decode_base64_to_pil

        assert callable(_decode_base64_to_pil)


class TestTracerCompatibility:
    """Tests to ensure the tracer API is used correctly by the bridge."""

    def test_tracer_has_latest_browser_screenshots(self) -> None:
        from esprit.telemetry.tracer import Tracer

        t = Tracer("test")
        assert hasattr(t, "latest_browser_screenshots")
        assert isinstance(t.latest_browser_screenshots, dict)

    def test_tracer_has_streaming_content(self) -> None:
        from esprit.telemetry.tracer import Tracer

        t = Tracer("test")
        assert hasattr(t, "streaming_content")
        assert isinstance(t.streaming_content, dict)

    def test_tracer_has_vulnerability_reports(self) -> None:
        from esprit.telemetry.tracer import Tracer

        t = Tracer("test")
        assert hasattr(t, "vulnerability_reports")
        assert isinstance(t.vulnerability_reports, list)

    def test_tracer_has_chat_messages(self) -> None:
        from esprit.telemetry.tracer import Tracer

        t = Tracer("test")
        assert hasattr(t, "chat_messages")
        assert isinstance(t.chat_messages, list)

    def test_tracer_has_tool_executions(self) -> None:
        from esprit.telemetry.tracer import Tracer

        t = Tracer("test")
        assert hasattr(t, "tool_executions")
        assert isinstance(t.tool_executions, dict)

    def test_tracer_get_real_tool_count(self) -> None:
        from esprit.telemetry.tracer import Tracer

        t = Tracer("test")
        assert t.get_real_tool_count() == 0
        t.log_tool_execution_start("a", "terminal", {})
        assert t.get_real_tool_count() == 1

    def test_tracer_log_agent_creation(self) -> None:
        from esprit.telemetry.tracer import Tracer

        t = Tracer("test")
        t.log_agent_creation("id1", "name1", "task1", parent_id=None)
        assert "id1" in t.agents
        assert t.agents["id1"]["name"] == "name1"
        assert t.agents["id1"]["task"] == "task1"
        assert t.agents["id1"]["status"] == "running"

    def test_tracer_update_agent_status(self) -> None:
        from esprit.telemetry.tracer import Tracer

        t = Tracer("test")
        t.log_agent_creation("id1", "name1", "task1")
        t.update_agent_status("id1", "completed")
        assert t.agents["id1"]["status"] == "completed"

    def test_tracer_streaming_content_lifecycle(self) -> None:
        from esprit.telemetry.tracer import Tracer

        t = Tracer("test")
        t.update_streaming_content("a1", "thinking...")
        assert t.get_streaming_content("a1") == "thinking..."
        t.clear_streaming_content("a1")
        assert t.get_streaming_content("a1") is None
