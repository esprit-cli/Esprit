"""Tests for the agent health popup interactions."""

from esprit.interface.tui import AgentHealthPopupScreen


def test_health_popup_scroll_offset_advances_for_offscreen_selection() -> None:
    screen = AgentHealthPopupScreen.__new__(AgentHealthPopupScreen)
    screen._selected_index = 5
    screen._get_health_rows = lambda: [{} for _ in range(8)]

    offset = AgentHealthPopupScreen._selected_scroll_offset(screen, viewport_height=8, current_scroll=0)

    assert offset > 0


def test_health_popup_scroll_offset_preserves_visible_selection() -> None:
    screen = AgentHealthPopupScreen.__new__(AgentHealthPopupScreen)
    screen._selected_index = 2
    screen._get_health_rows = lambda: [{} for _ in range(8)]

    offset = AgentHealthPopupScreen._selected_scroll_offset(screen, viewport_height=8, current_scroll=4)

    assert offset == 4


def test_health_popup_detail_includes_controls_and_activity() -> None:
    screen = AgentHealthPopupScreen.__new__(AgentHealthPopupScreen)
    screen._selected_index = 0
    screen._get_health_rows = lambda: [
        {
            "name": "Root Agent",
            "status": "llm_failed",
            "risk": "high",
            "last_output_age": "3m 38s",
            "error_streak": 1,
            "retry_count": 5,
            "snippet": "Transient LLM failure, retrying automatically.",
        }
    ]

    detail = AgentHealthPopupScreen._render_detail(screen)

    assert "Controls" in detail.plain
    assert "Latest Activity" in detail.plain
    assert "Transient LLM failure" in detail.plain
