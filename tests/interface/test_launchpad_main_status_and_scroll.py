from unittest.mock import MagicMock, patch

from esprit.interface.launchpad import LaunchpadApp, _MenuEntry


def test_main_entries_show_provider_and_model_hints() -> None:
    app = LaunchpadApp()
    app._project_name = "esprit"
    app._project_type = "Python"
    app._configured_provider_rows = lambda: [  # type: ignore[method-assign]
        ("OpenAI", "OAuth", "user@example.com"),
        ("Anthropic", "API Key", "API"),
    ]

    with patch(
        "esprit.interface.launchpad.Config.get",
        side_effect=lambda key: "openai/gpt-5" if key == "esprit_llm" else None,
    ):
        entries = app._build_main_entries()

    hints = {entry.key: entry.hint for entry in entries}
    assert hints["scan"] == "esprit (Python)"
    assert hints["model"] == "selected: gpt-5"
    assert hints["provider"].startswith("2 connected:")


def test_main_entries_show_empty_status_hints() -> None:
    app = LaunchpadApp()
    app._configured_provider_rows = lambda: []  # type: ignore[method-assign]

    with patch("esprit.interface.launchpad.Config.get", return_value=None):
        entries = app._build_main_entries()

    hints = {entry.key: entry.hint for entry in entries}
    assert hints["model"] == "selected: not selected"
    assert hints["provider"] == "none connected"


def test_menu_scroll_target_calculation() -> None:
    assert LaunchpadApp._get_menu_scroll_target(selected_index=9, top_row=0, visible_rows=4) == 6
    assert LaunchpadApp._get_menu_scroll_target(selected_index=1, top_row=3, visible_rows=4) == 1
    assert LaunchpadApp._get_menu_scroll_target(selected_index=4, top_row=2, visible_rows=4) is None


def test_ensure_selected_entry_visible_scrolls_to_selection() -> None:
    app = LaunchpadApp()
    app._current_entries = [_MenuEntry(f"model:{i}", f"Model {i}") for i in range(20)]
    app.selected_index = 10

    menu_widget = MagicMock()
    menu_widget.size.height = 6
    menu_widget.scroll_y = 0
    menu_widget.max_scroll_y = 30

    app._ensure_selected_entry_visible(menu_widget)

    menu_widget.scroll_to.assert_called_once_with(y=7, animate=False)


def test_ensure_selected_entry_visible_skips_scroll_when_visible() -> None:
    app = LaunchpadApp()
    app._current_entries = [_MenuEntry(f"model:{i}", f"Model {i}") for i in range(20)]
    app.selected_index = 3

    menu_widget = MagicMock()
    menu_widget.size.height = 6
    menu_widget.scroll_y = 0
    menu_widget.max_scroll_y = 30

    app._ensure_selected_entry_visible(menu_widget)

    menu_widget.scroll_to.assert_not_called()
