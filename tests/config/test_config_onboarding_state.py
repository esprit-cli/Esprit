import json

from esprit.config import Config


def _configure_temp_config_dir(monkeypatch, tmp_path):
    config_root = tmp_path / ".esprit"
    monkeypatch.setattr(Config, "config_dir", classmethod(lambda _cls: config_root))
    monkeypatch.setattr(Config, "_config_file_override", None)
    return config_root


def test_onboarding_state_defaults(monkeypatch, tmp_path) -> None:
    _configure_temp_config_dir(monkeypatch, tmp_path)

    state = Config.get_onboarding_state()

    assert state["version"] == 1
    assert state["state"] == "pending"
    assert state["completed_at"] is None
    assert state["last_seen_at"] is None
    assert state["skip_count"] == 0
    assert Config.is_onboarding_required(version=1) is True


def test_mark_onboarding_completed_persists(monkeypatch, tmp_path) -> None:
    config_root = _configure_temp_config_dir(monkeypatch, tmp_path)

    assert Config.mark_onboarding_completed(version=1) is True
    assert Config.is_onboarding_required(version=1) is False

    config_file = config_root / "cli-config.json"
    saved = json.loads(config_file.read_text(encoding="utf-8"))
    onboarding = saved["ui"]["onboarding"]

    assert onboarding["state"] == "completed"
    assert onboarding["version"] == 1
    assert isinstance(onboarding["completed_at"], str)
    assert isinstance(onboarding["last_seen_at"], str)
    assert onboarding["skip_count"] == 0


def test_mark_onboarding_skipped_increments_skip_count(monkeypatch, tmp_path) -> None:
    _configure_temp_config_dir(monkeypatch, tmp_path)

    assert Config.mark_onboarding_skipped(version=1) is True
    first = Config.get_onboarding_state()
    assert first["state"] == "skipped"
    assert first["skip_count"] == 1
    assert Config.is_onboarding_required(version=1) is True

    assert Config.mark_onboarding_skipped(version=1) is True
    second = Config.get_onboarding_state()
    assert second["skip_count"] == 2
    assert Config.is_onboarding_required(version=1) is True


def test_onboarding_version_bump_requires_rerun(monkeypatch, tmp_path) -> None:
    _configure_temp_config_dir(monkeypatch, tmp_path)
    assert Config.mark_onboarding_completed(version=1) is True

    assert Config.is_onboarding_required(version=1) is False
    assert Config.is_onboarding_required(version=2) is True
