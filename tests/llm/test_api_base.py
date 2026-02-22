"""Tests for provider API base resolution."""

from esprit.llm.api_base import resolve_api_base


def test_resolve_api_base_uses_opencode_default(monkeypatch) -> None:
    monkeypatch.setattr("esprit.llm.api_base.Config.get", lambda *_args, **_kwargs: None)

    assert resolve_api_base("opencode/gpt-5.1-codex") == "https://opencode.ai/zen/v1"


def test_resolve_api_base_supports_zen_alias(monkeypatch) -> None:
    monkeypatch.setattr("esprit.llm.api_base.Config.get", lambda *_args, **_kwargs: None)

    assert resolve_api_base("zen/gpt-5.1-codex") == "https://opencode.ai/zen/v1"


def test_explicit_api_base_still_wins(monkeypatch) -> None:
    def fake_get(key: str, _default=None):
        if key == "llm_api_base":
            return "https://proxy.example/v1"
        return None

    monkeypatch.setattr("esprit.llm.api_base.Config.get", fake_get)

    assert resolve_api_base("opencode/gpt-5") == "https://proxy.example/v1"
