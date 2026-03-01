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
    assert hints["scan_mode"] == "selected: deep"


def test_main_entries_show_empty_status_hints() -> None:
    app = LaunchpadApp()
    app._configured_provider_rows = lambda: []  # type: ignore[method-assign]

    with patch("esprit.interface.launchpad.Config.get", return_value=None):
        entries = app._build_main_entries()

    hints = {entry.key: entry.hint for entry in entries}
    assert hints["model"] == "selected: not selected"
    assert hints["provider"] == "none connected"
    assert hints["scan_mode"] == "selected: deep"


def test_main_entries_show_current_scan_mode_hint() -> None:
    app = LaunchpadApp()
    app._scan_mode = "quick"
    app._configured_provider_rows = lambda: []  # type: ignore[method-assign]

    with patch("esprit.interface.launchpad.Config.get", return_value=None):
        entries = app._build_main_entries()

    hints = {entry.key: entry.hint for entry in entries}
    assert hints["scan_mode"] == "selected: quick"


def test_render_menu_scrolls_top_row_to_keep_selected_entry_visible() -> None:
    app = LaunchpadApp()
    app._current_entries = [_MenuEntry(f"model:{i}", f"Model {i}") for i in range(20)]
    menu_widget = MagicMock()
    app.query_one = lambda _selector, _widget_type=None: menu_widget  # type: ignore[method-assign]

    app._MENU_VISIBLE_ROWS = 4
    app._menu_top_row = 0

    app.selected_index = 9
    app._render_menu()
    assert app._menu_top_row == 6

    app._menu_top_row = 3
    app.selected_index = 1
    app._render_menu()
    assert app._menu_top_row == 1

    app._menu_top_row = 2
    app.selected_index = 4
    app._render_menu()
    assert app._menu_top_row == 2


def test_select_entry_by_key_sets_selected_index() -> None:
    app = LaunchpadApp()
    app._current_entries = [
        _MenuEntry("theme:esprit", "Esprit"),
        _MenuEntry("theme:matrix", "Matrix"),
        _MenuEntry("back", "Back"),
    ]

    changed = app._select_entry_by_key("theme:matrix")

    assert changed is True
    assert app.selected_index == 1


def test_select_entry_by_key_returns_false_when_missing() -> None:
    app = LaunchpadApp()
    app.selected_index = 2
    app._current_entries = [
        _MenuEntry("scan_mode:quick", "Quick"),
        _MenuEntry("scan_mode:deep", "Deep"),
        _MenuEntry("back", "Back"),
    ]

    changed = app._select_entry_by_key("scan_mode:standard")

    assert changed is False
    assert app.selected_index == 2
