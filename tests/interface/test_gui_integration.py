"""Tests for GUI server creation and main.py integration."""

import argparse
from unittest.mock import MagicMock, patch

import pytest


class TestGUIServerAlwaysCreated:
    """Tests that the GUI server is always created without needing a flag."""

    def test_scan_parser_no_gui_flag(self) -> None:
        """Verify --gui flag no longer exists on the scan subcommand."""
        from esprit.interface.main import parse_arguments

        with patch("sys.argv", ["esprit", "scan", "https://example.com"]):
            args = parse_arguments()
            assert not hasattr(args, "gui")

    def test_legacy_parser_no_gui_flag(self) -> None:
        """Verify --gui flag no longer exists on the legacy parser."""
        from esprit.interface.main import parse_arguments

        with patch("sys.argv", ["esprit", "--target", "https://example.com"]):
            args = parse_arguments()
            assert not hasattr(args, "gui")


class TestGUIServerCreation:
    """Tests for GUI server creation in main()."""

    def test_gui_server_import_guard(self) -> None:
        """Verify GUIServer can be imported from the gui package."""
        from esprit.gui import GUIServer

        server = GUIServer(port=9999)
        assert server.port == 9999

    def test_gui_init_module(self) -> None:
        """Verify __init__.py exports GUIServer."""
        import esprit.gui

        assert hasattr(esprit.gui, "GUIServer")
        assert "GUIServer" in esprit.gui.__all__


class TestRunTuiAcceptsGUIServer:
    """Tests for run_tui function signature."""

    def test_run_tui_accepts_gui_server_none(self) -> None:
        """Verify run_tui accepts gui_server=None (default)."""
        import inspect

        from esprit.interface.tui import run_tui

        sig = inspect.signature(run_tui)
        assert "gui_server" in sig.parameters
        assert sig.parameters["gui_server"].default is None

    def test_esprit_tui_app_accepts_gui_server(self) -> None:
        """Verify EspritTUIApp.__init__ accepts gui_server parameter."""
        import inspect

        from esprit.interface.tui import EspritTUIApp

        sig = inspect.signature(EspritTUIApp.__init__)
        assert "gui_server" in sig.parameters
