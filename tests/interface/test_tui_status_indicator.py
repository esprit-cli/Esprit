"""Tests for compact running-status ghost indicator."""

from types import SimpleNamespace

from rich.text import Text

from esprit.interface.tui import EspritTUIApp


def test_running_ghost_indicator_pulses_with_spinner_frame() -> None:
    app = EspritTUIApp.__new__(EspritTUIApp)
    app._spinner_frame_index = 0

    frame_0 = EspritTUIApp._build_running_ghost_indicator(app).plain

    app._spinner_frame_index = 1
    frame_1 = EspritTUIApp._build_running_ghost_indicator(app).plain

    assert frame_0.startswith("👻")
    assert frame_1.startswith("👻")
    assert frame_0 != frame_1


def test_running_status_text_includes_mini_ghost_indicator() -> None:
    app = EspritTUIApp.__new__(EspritTUIApp)
    app._spinner_frame_index = 0
    app.tracer = SimpleNamespace(compacting_agents=set())
    app._agent_has_real_activity = lambda _agent_id: False
    app._get_animated_verb_text = lambda _agent_id, verb: Text(verb)
    app._sweep_colors = []
    app._compact_sweep_colors = []

    content, _keymap, should_animate = EspritTUIApp._get_status_display_content(
        app,
        "agent_1",
        {"status": "running"},
    )

    assert content is not None
    assert content.plain.startswith("👻")
    assert "Initializing" in content.plain
    assert should_animate is True
