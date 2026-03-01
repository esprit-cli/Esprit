from pathlib import Path


def _load_styles() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / "esprit/interface/assets/launchpad_styles.tcss").read_text(encoding="utf-8")


def test_launchpad_has_compact_aligned_layout_rules() -> None:
    styles = _load_styles()
    assert "#launchpad_title {" in styles
    assert "content-align: left middle;" in styles
    assert "text-align: left;" in styles
    assert "#launchpad_menu {" in styles
    assert "max-height: 18;" in styles
    assert "scrollbar-size-vertical: 0;" in styles
    assert "#launchpad_input {" in styles
    assert "height: 3;" in styles
    assert "min-height: 3;" in styles
    assert "#launchpad_status {" in styles
    assert "#launchpad_hint {" in styles
    assert "padding: 0 2;" in styles


def test_launchpad_theme_blocks_define_menu_scrollbar_colors() -> None:
    styles = _load_styles()
    assert "Screen.theme-esprit #launchpad_menu {" in styles
    assert "Screen.theme-ember #launchpad_menu {" in styles
    assert "Screen.theme-matrix #launchpad_menu {" in styles
    assert "Screen.theme-glacier #launchpad_menu {" in styles
    assert "Screen.theme-crt #launchpad_menu {" in styles
    assert "Screen.theme-sakura #launchpad_menu {" in styles
    assert "scrollbar-background:" in styles
    assert "scrollbar-color:" in styles
