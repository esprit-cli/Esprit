"""Tests for the GUI server module."""

from unittest.mock import MagicMock

from esprit.telemetry.tracer import Tracer


class TestGUIServerInit:
    """Tests for GUIServer initialization."""

    def test_default_port(self) -> None:
        from esprit.gui.server import GUIServer

        server = GUIServer()
        assert server.port == 7860

    def test_custom_port(self) -> None:
        from esprit.gui.server import GUIServer

        server = GUIServer(port=9090)
        assert server.port == 9090

    def test_initial_state(self) -> None:
        from esprit.gui.server import GUIServer

        server = GUIServer()
        assert server._thread is None
        assert server._loop is None
        assert server._bridge is None
        assert not server._started.is_set()
        assert not server._should_stop.is_set()

    def test_get_url(self) -> None:
        from esprit.gui.server import GUIServer

        server = GUIServer(port=8080)
        assert server.get_url() == "http://localhost:8080"

    def test_stop_before_start(self) -> None:
        from esprit.gui.server import GUIServer

        server = GUIServer()
        # Should not raise
        server.stop()

    def test_double_start_guard(self) -> None:
        from esprit.gui.server import GUIServer

        server = GUIServer()
        # Mock the thread as alive
        mock_thread = MagicMock()
        mock_thread.is_alive.return_value = True
        server._thread = mock_thread

        tracer = Tracer("test")
        server.start(tracer, open_browser=False)
        # Should not create a new thread
        assert server._thread is mock_thread


class TestGUIServerStaticDir:
    """Tests for static file directory configuration."""

    def test_static_dir_exists(self) -> None:
        from esprit.gui.server import _STATIC_DIR

        assert _STATIC_DIR.exists()
        assert _STATIC_DIR.is_dir()

    def test_static_files_present(self) -> None:
        from esprit.gui.server import _STATIC_DIR

        assert (_STATIC_DIR / "index.html").exists()
        assert (_STATIC_DIR / "app.js").exists()
        assert (_STATIC_DIR / "style.css").exists()

    def test_index_html_content(self) -> None:
        from esprit.gui.server import _STATIC_DIR

        content = (_STATIC_DIR / "index.html").read_text()
        assert "Esprit Dashboard" in content
        assert "browser-viewer" in content
        assert "agents-panel" in content
        assert "vulns-panel" in content

    def test_index_html_contains_activity_strip_ids(self) -> None:
        from esprit.gui.server import _STATIC_DIR

        content = (_STATIC_DIR / "index.html").read_text()
        assert "status-activity" in content
        assert "status-activity-text" in content
        assert "status-activity-metrics" in content
        assert "scan-config-live" in content

    def test_app_js_safe_dom_methods(self) -> None:
        """Verify app.js uses safe DOM methods for content rendering."""
        from esprit.gui.server import _STATIC_DIR

        content = (_STATIC_DIR / "app.js").read_text()
        # Verify safe DOM methods are used
        assert "createElement" in content
        assert "textContent" in content
        assert "appendChild" in content

    def test_app_js_contains_activity_strip_renderer_markers(self) -> None:
        from esprit.gui.server import _STATIC_DIR

        content = (_STATIC_DIR / "app.js").read_text()
        assert "_renderActivityStrip" in content
        assert "HEX_SPINNER_FRAMES" in content

    def test_style_css_dark_theme(self) -> None:
        from esprit.gui.server import _STATIC_DIR

        content = (_STATIC_DIR / "style.css").read_text()
        assert "#050505" in content  # Dark background
        assert "#22d3ee" in content  # Accent color

    def test_style_css_contains_activity_strip_anti_shift_rules(self) -> None:
        from esprit.gui.server import _STATIC_DIR

        content = (_STATIC_DIR / "style.css").read_text()
        assert "#status-activity" in content
        assert "font-variant-numeric: tabular-nums" in content
        assert "text-overflow: ellipsis" in content

    def test_style_css_browser_hidden_by_default(self) -> None:
        """Browser viewer starts hidden, only shown when screenshots exist."""
        from esprit.gui.server import _STATIC_DIR

        content = (_STATIC_DIR / "style.css").read_text()
        assert "display: none" in content  # browser-viewer hidden by default
        assert ".visible" in content  # class to show it
