from esprit.config import Config
from esprit.interface.launchpad import LaunchpadApp


def test_invalid_saved_theme_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.setattr(
        Config,
        "get_launchpad_theme",
        classmethod(lambda _cls: "unknown-theme"),
    )

    app = LaunchpadApp()

    assert app._theme_id == LaunchpadApp.DEFAULT_THEME


def test_theme_menu_marks_active_theme(monkeypatch) -> None:
    monkeypatch.setattr(
        Config,
        "get_launchpad_theme",
        classmethod(lambda _cls: "matrix"),
    )

    app = LaunchpadApp()
    entries = app._build_theme_entries()

    matrix = next(entry for entry in entries if entry.key == "theme:matrix")
    esprit = next(entry for entry in entries if entry.key == "theme:esprit")

    assert matrix.label.startswith("●")
    assert esprit.label.startswith("○")


def test_set_theme_persists_only_when_changed(monkeypatch) -> None:
    monkeypatch.setattr(
        Config,
        "get_launchpad_theme",
        classmethod(lambda _cls: "esprit"),
    )

    saved_themes: list[str] = []
    monkeypatch.setattr(
        Config,
        "save_launchpad_theme",
        classmethod(lambda _cls, theme: saved_themes.append(theme) or True),
    )

    app = LaunchpadApp()
    messages: list[str] = []
    app._set_status = messages.append

    changed = app._set_theme("glacier", persist=True)
    unchanged = app._set_theme("glacier", persist=True)

    assert changed is True
    assert unchanged is False
    assert app._theme_id == "glacier"
    assert saved_themes == ["glacier"]
    assert messages[-1] == "Theme set: Glacier"
