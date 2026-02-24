import json

from esprit.config import Config


def _configure_temp_config_dir(monkeypatch, tmp_path):
    config_root = tmp_path / ".esprit"
    monkeypatch.setattr(Config, "config_dir", classmethod(lambda _cls: config_root))
    monkeypatch.setattr(Config, "_config_file_override", None)
    return config_root


def test_launchpad_theme_round_trip(monkeypatch, tmp_path) -> None:
    config_root = _configure_temp_config_dir(monkeypatch, tmp_path)

    assert Config.get_launchpad_theme() == "esprit"
    assert Config.save_launchpad_theme("matrix") is True

    config_file = config_root / "cli-config.json"
    saved = json.loads(config_file.read_text(encoding="utf-8"))

    assert saved["ui"]["launchpad_theme"] == "matrix"
    assert Config.get_launchpad_theme() == "matrix"


def test_save_current_keeps_ui_section(monkeypatch, tmp_path) -> None:
    config_root = _configure_temp_config_dir(monkeypatch, tmp_path)
    config_root.mkdir(parents=True, exist_ok=True)
    config_file = config_root / "cli-config.json"
    config_file.write_text(
        json.dumps({"env": {"ESPRIT_LLM": "openai/gpt-5"}, "ui": {"launchpad_theme": "glacier"}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("ESPRIT_LLM", "openai/gpt-5.2")

    assert Config.save_current() is True

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert saved["env"]["ESPRIT_LLM"] == "openai/gpt-5.2"
    assert saved["ui"]["launchpad_theme"] == "glacier"


def test_apply_saved_keeps_ui_section_when_env_is_rewritten(monkeypatch, tmp_path) -> None:
    config_root = _configure_temp_config_dir(monkeypatch, tmp_path)
    config_root.mkdir(parents=True, exist_ok=True)
    config_file = config_root / "cli-config.json"
    config_file.write_text(
        json.dumps({"env": {"ESPRIT_LLM": "openai/gpt-5"}, "ui": {"launchpad_theme": "ember"}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("ESPRIT_LLM", "")

    Config.apply_saved(force=True)

    saved = json.loads(config_file.read_text(encoding="utf-8"))
    assert "ESPRIT_LLM" not in saved["env"]
    assert saved["ui"]["launchpad_theme"] == "ember"
