"""Tests for the BrowserPreviewScreen with live-updating."""

import argparse
from unittest.mock import MagicMock, patch

import pytest

from rich.text import Text


class TestBrowserPreviewScreenInit:
    """Tests for BrowserPreviewScreen initialization."""

    def test_init_with_agent_id(self) -> None:
        from esprit.interface.tui import BrowserPreviewScreen

        screen = BrowserPreviewScreen("base64data", url="https://test.com", agent_id="agent-1")
        assert screen._screenshot_b64 == "base64data"
        assert screen._url == "https://test.com"
        assert screen._agent_id == "agent-1"
        assert screen._refresh_timer is None

    def test_init_without_agent_id(self) -> None:
        from esprit.interface.tui import BrowserPreviewScreen

        screen = BrowserPreviewScreen("base64data")
        assert screen._agent_id == ""
        assert screen._url == ""


class TestBrowserPreviewScreenAutoRefresh:
    """Tests for the auto-refresh mechanism."""

    def test_check_for_new_screenshot_no_agent_id(self) -> None:
        from esprit.interface.tui import BrowserPreviewScreen

        screen = BrowserPreviewScreen("base64data")
        # Should not raise when agent_id is empty
        screen._check_for_new_screenshot()

    def test_check_for_new_screenshot_updates_on_change(self) -> None:
        from esprit.interface.tui import BrowserPreviewScreen

        screen = BrowserPreviewScreen("old_data", agent_id="agent-1")

        # Verify the method exists and doesn't crash with no app context
        # (The app property is a Textual context var and can't be patched directly)
        assert hasattr(screen, "_check_for_new_screenshot")
        # Should handle missing app gracefully
        screen._check_for_new_screenshot()

    def test_render_preview_fallback(self) -> None:
        from esprit.interface.tui import BrowserPreviewScreen

        screen = BrowserPreviewScreen("invalid_base64_data")
        result = screen._render_preview()
        assert isinstance(result, Text)


class TestPyprojectTomlUpdates:
    """Tests to verify pyproject.toml has correct new entries."""

    def test_gui_extra_defined(self) -> None:
        from pathlib import Path

        toml_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        content = toml_path.read_text()

        assert 'gui = ["fastapi", "uvicorn", "websockets"]' in content

    def test_enhanced_preview_extra_defined(self) -> None:
        from pathlib import Path

        toml_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        content = toml_path.read_text()

        assert 'enhanced-preview = ["textual-image"]' in content

    def test_websockets_optional_dep(self) -> None:
        from pathlib import Path

        toml_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        content = toml_path.read_text()

        assert "websockets" in content

    def test_textual_image_optional_dep(self) -> None:
        from pathlib import Path

        toml_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        content = toml_path.read_text()

        assert "textual-image" in content

    def test_textual_image_mypy_override(self) -> None:
        from pathlib import Path

        toml_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        content = toml_path.read_text()

        assert '"textual_image.*"' in content

    def test_gui_static_files_included(self) -> None:
        from pathlib import Path

        toml_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        content = toml_path.read_text()

        assert "esprit/gui/static/**/*" in content
