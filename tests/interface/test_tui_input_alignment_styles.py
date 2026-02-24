from pathlib import Path


def _load_styles() -> str:
    root = Path(__file__).resolve().parents[2]
    return (root / "esprit/interface/assets/tui_styles.tcss").read_text(encoding="utf-8")


def test_chat_input_and_status_row_are_center_aligned() -> None:
    styles = _load_styles()
    assert "#chat_input_container {" in styles
    assert "align-vertical: middle;" in styles
    assert "#chat_prompt {" in styles
    assert "content-align: left middle;" in styles
    assert "#agent_status_display {" in styles
    assert "height: 2;" in styles
    assert "align-vertical: top;" not in styles


def test_theme_blocks_include_chat_input_container_overrides() -> None:
    styles = _load_styles()
    assert "Screen.theme-esprit #chat_input_container {" in styles
    assert "Screen.theme-ember #chat_input_container {" in styles
    assert "Screen.theme-matrix #chat_input_container {" in styles
    assert "Screen.theme-glacier #chat_input_container {" in styles
    assert "Screen.theme-crt #chat_input_container {" in styles
