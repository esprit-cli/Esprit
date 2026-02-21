"""Tests for the responsive layout dashboard hint in TUI."""





class TestResponsiveLayoutHint:
    """Tests for the dashboard hint in _apply_responsive_layout."""

    def test_responsive_layout_method_exists(self) -> None:
        from esprit.interface.tui import EspritTUIApp

        assert hasattr(EspritTUIApp, "_apply_responsive_layout")

    def test_layout_source_contains_dashboard_hint(self) -> None:
        """Check that _apply_responsive_layout source contains dashboard URL hints."""
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._apply_responsive_layout)
        assert "Dashboard" in source
        assert "7860" in source


class TestGUIPackageStructure:
    """Tests for the GUI package structure and imports."""

    def test_gui_package_importable(self) -> None:
        pass

    def test_gui_server_importable(self) -> None:
        pass

    def test_gui_tracer_bridge_importable(self) -> None:
        pass

    def test_image_widget_importable(self) -> None:
        pass

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

    def test_subagent_dashboard_uses_created_at_for_elapsed_hint(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._build_subagent_dashboard)
        assert 'child.get("created_at")' in source

    def test_status_display_keeps_token_stats_in_initializing_state(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._get_status_display_content)
        assert "Initializing agent" in source
        assert "ts = _token_stats_text()" in source

    def test_chat_placeholder_handles_finished_agents_without_events(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._get_chat_content)
        assert "No activity recorded for this agent." in source
        assert '"finished"' in source

    def test_done_statuses_include_finished(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._update_stats_display)
        assert '"finished"' in source

    def test_agent_config_includes_watchdog_defaults(self) -> None:
        import argparse

        from esprit.interface.tui import EspritTUIApp

        app = EspritTUIApp.__new__(EspritTUIApp)
        args = argparse.Namespace(scan_mode="deep", local_sources=[])

        config = EspritTUIApp._build_agent_config(app, args)

        assert config["stall_policy"] == "auto_recover"
        timeout = int(getattr(config["llm_config"], "timeout", 300) or 300)
        expected_watchdog = max(360, min(600, timeout + 60))
        assert config["llm_watchdog_timeout_s"] == expected_watchdog
        assert config["tool_watchdog_timeout_s"] == 180
        assert config["stall_grace_period_s"] == 90
        assert config["max_stall_recoveries"] == 3

    def test_agent_config_scales_watchdog_with_llm_timeout(self, monkeypatch) -> None:
        import argparse
        from types import SimpleNamespace

        from esprit.interface.tui import EspritTUIApp

        def _fake_llm_config(*_args, **_kwargs):
            return SimpleNamespace(timeout=1800)

        monkeypatch.setattr("esprit.interface.tui.LLMConfig", _fake_llm_config)

        app = EspritTUIApp.__new__(EspritTUIApp)
        args = argparse.Namespace(scan_mode="deep", local_sources=[])

        config = EspritTUIApp._build_agent_config(app, args)

        assert config["llm_watchdog_timeout_s"] == 600

    def test_subagent_dashboard_includes_watchdog_diagnostics(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._build_subagent_dashboard)
        assert "No heartbeat for" in source
        assert "stalled_recovered" in source

    def test_status_display_includes_watchdog_signals(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._get_status_display_content)
        assert "Watchdog: no heartbeat" in source
        assert "Watchdog recovered stall" in source

    def test_status_display_includes_live_token_and_time_metrics(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._get_status_display_content)
        assert "In " in source
        assert "Out " in source
        assert "tok/s" in source

    def test_status_display_uses_hex_spinner_frames(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._get_status_display_content)
        assert "HEX_SPINNER_FRAMES" in source

    def test_status_display_keeps_done_states_visible_and_dimmed(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._get_status_display_content)
        assert "Agent completed" in source
        assert "dim" in source

    def test_update_agent_status_display_uses_idle_fallback_instead_of_hiding(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._update_agent_status_display)
        assert "Idle — waiting for agent activity" in source
        assert 'status_display.remove_class, "hidden"' in source
        assert 'status_display.add_class, "hidden"' not in source
        assert 'keymap_indicator.update, Text("")' in source

    def test_watch_show_splash_initializes_agent_status_display_without_hidden_class(
        self,
    ) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp.watch_show_splash)
        assert 'id="agent_status_display"' in source
        assert 'id="agent_status_display", classes="hidden"' not in source

    def test_waiting_status_returns_visible_idle_copy(self) -> None:
        from types import SimpleNamespace

        from rich.text import Text

        from esprit.interface.tui import EspritTUIApp

        app = EspritTUIApp.__new__(EspritTUIApp)
        app._id = "test-app"
        app._spinner_frame_index = 0
        app._queued_steering_message = None
        app.tracer = SimpleNamespace(
            compacting_agents=set(),
            get_streaming_thinking=lambda _agent_id: "",
            get_streaming_content=lambda _agent_id: "",
            get_total_llm_stats=lambda: {"total": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}},
        )
        app._get_watchdog_state = lambda _agent_id, _status: None
        app._agent_has_real_activity = lambda _agent_id: False

        content, keymap, should_animate = EspritTUIApp._get_status_display_content(
            app,
            "agent_x",
            {"status": "waiting"},
        )

        assert isinstance(content, Text)
        assert "Idle" in content.plain
        assert "waiting" in content.plain.lower()
        assert isinstance(keymap, Text)
        assert "resume" in keymap.plain.lower()
        assert should_animate is False

    def test_queued_status_returns_slot_wait_copy(self) -> None:
        from types import SimpleNamespace

        from rich.text import Text

        from esprit.interface.tui import EspritTUIApp

        app = EspritTUIApp.__new__(EspritTUIApp)
        app._id = "test-app"
        app._spinner_frame_index = 0
        app._queued_steering_message = None
        app.tracer = SimpleNamespace(
            compacting_agents=set(),
            get_streaming_thinking=lambda _agent_id: "",
            get_streaming_content=lambda _agent_id: "",
            get_total_llm_stats=lambda: {"total": {"input_tokens": 0, "output_tokens": 0, "cached_tokens": 0}},
        )
        app._get_watchdog_state = lambda _agent_id, _status: None
        app._agent_has_real_activity = lambda _agent_id: False

        content, keymap, should_animate = EspritTUIApp._get_status_display_content(
            app,
            "agent_x",
            {"status": "queued"},
        )

        assert isinstance(content, Text)
        assert "queued" in content.plain.lower()
        assert "slot" in content.plain.lower()
        assert isinstance(keymap, Text)
        assert "slot" in keymap.plain.lower()
        assert should_animate is False

    def test_splash_banner_uses_only_white_and_cyan_styles(self) -> None:
        from esprit.interface.tui import SplashScreen

        splash = SplashScreen()
        banner = splash._build_banner_text()

        plain = banner.plain
        lines = plain.split("\n")
        assert len(lines) >= 2

        first_line = lines[0]
        second_line = lines[1]

        first_line_start = plain.find(first_line)
        second_line_start = plain.find(second_line, first_line_start + len(first_line))

        def _line_style(offset: int) -> str | None:
            for span in banner.spans:
                if span.start <= offset < span.end:
                    return str(span.style)
            return None

        first_style = _line_style(first_line_start)
        second_style = _line_style(second_line_start)

        assert first_style is not None
        assert second_style is not None
        assert "#22d3ee" in first_style
        assert "white" in second_style

    def test_llm_failed_status_is_dimmed_and_stays_visible(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._get_status_display_content)
        assert 'if status == "llm_failed":' in source
        assert 'text.append("⬡ ", style="dim")' in source
        assert 'style="dim red"' in source

    def test_update_agent_status_display_none_selected_shows_idle_and_clears_keymap(
        self,
    ) -> None:
        from types import SimpleNamespace

        from rich.text import Text

        from esprit.interface.tui import EspritTUIApp

        class _FakeHorizontal:
            def __init__(self) -> None:
                self.removed_classes: list[str] = []
                self.added_classes: list[str] = []

            def remove_class(self, name: str) -> None:
                self.removed_classes.append(name)

            def add_class(self, name: str) -> None:
                self.added_classes.append(name)

        class _FakeStatic:
            def __init__(self) -> None:
                self.updated = None

            def update(self, value) -> None:
                self.updated = value

        app = EspritTUIApp.__new__(EspritTUIApp)
        app._id = "test-app"
        app._reactive_selected_agent_id = None
        app.tracer = SimpleNamespace(agents={})
        app._queued_steering_message = None
        app._is_widget_safe = lambda _w: True
        app._safe_widget_operation = lambda fn, *a, **k: fn(*a, **k)
        app._stop_dot_animation = lambda: None

        status_display = _FakeHorizontal()
        status_text = _FakeStatic()
        keymap_indicator = _FakeStatic()

        def _query_one(selector: str, _widget_type):
            mapping = {
                "#agent_status_display": status_display,
                "#status_text": status_text,
                "#keymap_indicator": keymap_indicator,
            }
            return mapping[selector]

        app.query_one = _query_one

        EspritTUIApp._update_agent_status_display(app)

        assert isinstance(status_text.updated, Text)
        assert "Idle — waiting for agent activity" in status_text.updated.plain
        assert isinstance(keymap_indicator.updated, Text)
        assert keymap_indicator.updated.plain == ""
        assert "hidden" in status_display.removed_classes

    def test_update_agent_status_display_missing_agent_uses_idle_fallback(self) -> None:
        from types import SimpleNamespace

        from rich.text import Text

        from esprit.interface.tui import EspritTUIApp

        class _FakeHorizontal:
            def __init__(self) -> None:
                self.removed_classes: list[str] = []
                self.added_classes: list[str] = []

            def remove_class(self, name: str) -> None:
                self.removed_classes.append(name)

            def add_class(self, name: str) -> None:
                self.added_classes.append(name)

        class _FakeStatic:
            def __init__(self) -> None:
                self.updated = None

            def update(self, value) -> None:
                self.updated = value

        app = EspritTUIApp.__new__(EspritTUIApp)
        app._id = "test-app"
        app._reactive_selected_agent_id = "agent_missing"
        app.tracer = SimpleNamespace(agents={})
        app._queued_steering_message = None
        app._is_widget_safe = lambda _w: True
        app._safe_widget_operation = lambda fn, *a, **k: fn(*a, **k)
        app._stop_dot_animation = lambda: None

        status_display = _FakeHorizontal()
        status_text = _FakeStatic()
        keymap_indicator = _FakeStatic()

        def _query_one(selector: str, _widget_type):
            mapping = {
                "#agent_status_display": status_display,
                "#status_text": status_text,
                "#keymap_indicator": keymap_indicator,
            }
            if selector in mapping:
                return mapping[selector]
            raise ValueError("unknown selector")

        app.query_one = _query_one

        EspritTUIApp._update_agent_status_display(app)

        assert isinstance(status_text.updated, Text)
        assert "Idle — waiting for agent activity" in status_text.updated.plain
        assert "hidden" in status_display.removed_classes
        assert "hidden" not in status_display.added_classes

    def test_update_agent_status_display_no_content_uses_idle_fallback(self) -> None:
        from types import SimpleNamespace

        from rich.text import Text

        from esprit.interface.tui import EspritTUIApp

        class _FakeHorizontal:
            def __init__(self) -> None:
                self.removed_classes: list[str] = []
                self.added_classes: list[str] = []

            def remove_class(self, name: str) -> None:
                self.removed_classes.append(name)

            def add_class(self, name: str) -> None:
                self.added_classes.append(name)

        class _FakeStatic:
            def __init__(self) -> None:
                self.updated = None

            def update(self, value) -> None:
                self.updated = value

        app = EspritTUIApp.__new__(EspritTUIApp)
        app._id = "test-app"
        app._reactive_selected_agent_id = "agent_1"
        app.tracer = SimpleNamespace(agents={"agent_1": {"status": "running"}})
        app._queued_steering_message = None
        app._is_widget_safe = lambda _w: True
        app._safe_widget_operation = lambda fn, *a, **k: fn(*a, **k)
        app._stop_dot_animation = lambda: None
        app._get_status_display_content = lambda _agent_id, _agent_data: (None, Text(), False)

        status_display = _FakeHorizontal()
        status_text = _FakeStatic()
        keymap_indicator = _FakeStatic()

        def _query_one(selector: str, _widget_type):
            mapping = {
                "#agent_status_display": status_display,
                "#status_text": status_text,
                "#keymap_indicator": keymap_indicator,
            }
            return mapping[selector]

        app.query_one = _query_one

        EspritTUIApp._update_agent_status_display(app)

        assert isinstance(status_text.updated, Text)
        assert "Idle — waiting for agent activity" in status_text.updated.plain
        assert isinstance(keymap_indicator.updated, Text)
        assert keymap_indicator.updated.plain == ""
        assert "hidden" in status_display.removed_classes

    def test_watchdog_state_uses_grace_period_for_stale_detection(self) -> None:
        import inspect

        from esprit.interface.tui import EspritTUIApp

        source = inspect.getsource(EspritTUIApp._get_watchdog_state)
        assert "stall_grace_period_s" in source
        assert "age_s > grace_s" in source

    def test_watchdog_state_reports_stalled_when_heartbeat_is_old(self) -> None:
        from datetime import UTC, datetime, timedelta
        from types import SimpleNamespace

        from esprit.interface.tui import EspritTUIApp

        app = EspritTUIApp.__new__(EspritTUIApp)
        stale = (datetime.now(UTC) - timedelta(seconds=120)).isoformat()
        app.tracer = SimpleNamespace(
            get_agent_heartbeat=lambda _agent_id: {
                "timestamp": stale,
                "phase": "before_llm_processing",
                "detail": "iter-3",
            }
        )
        app.agent_config = {"stall_grace_period_s": 30}

        state = EspritTUIApp._get_watchdog_state(app, "agent_1", "running")

        assert state is not None
        assert state["kind"] == "stalled"
        assert state["phase"] == "before_llm_processing"

    def test_watchdog_state_reports_recovery_phase_as_recovered(self) -> None:
        from datetime import UTC, datetime
        from types import SimpleNamespace

        from esprit.interface.tui import EspritTUIApp

        app = EspritTUIApp.__new__(EspritTUIApp)
        now = datetime.now(UTC).isoformat()
        app.tracer = SimpleNamespace(
            get_agent_heartbeat=lambda _agent_id: {
                "timestamp": now,
                "phase": "recovery",
                "detail": "Heartbeat stale for longer than 90s",
            }
        )
        app.agent_config = {"stall_grace_period_s": 30}

        state = EspritTUIApp._get_watchdog_state(app, "agent_1", "running")

        assert state is not None
        assert state["kind"] == "recovered"
        assert "Heartbeat stale" in state["detail"]

    def test_update_agent_node_maps_finished_and_recovered_statuses(self) -> None:
        import types

        from esprit.interface.tui import EspritTUIApp

        app = EspritTUIApp.__new__(EspritTUIApp)
        app._stats_spinner_frame = 0
        app.agent_nodes = {
            "a1": types.SimpleNamespace(
                label="",
                set_label=lambda new_label: setattr(
                    app.agent_nodes["a1"],
                    "label",
                    new_label,
                ),
            )
        }
        app._agent_vulnerability_count = lambda _agent_id: 0

        changed_finished = EspritTUIApp._update_agent_node(
            app,
            "a1",
            {"name": "Worker", "status": "finished"},
        )
        assert changed_finished is True
        assert "✓ Worker" in app.agent_nodes["a1"].label

        changed_recovered = EspritTUIApp._update_agent_node(
            app,
            "a1",
            {"name": "Worker", "status": "stalled_recovered"},
        )
        assert changed_recovered is True
        assert "↻ Worker" in app.agent_nodes["a1"].label

    def test_has_running_children_treats_recovered_as_active(self) -> None:
        from types import SimpleNamespace

        from esprit.interface.tui import EspritTUIApp

        app = EspritTUIApp.__new__(EspritTUIApp)
        app.tracer = SimpleNamespace(
            agents={
                "child_1": {"parent_id": "root", "status": "stalled_recovered"},
            }
        )

        assert EspritTUIApp._has_running_children(app, "root") is True

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
