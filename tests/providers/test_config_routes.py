import json
from importlib import import_module
from pathlib import Path


provider_config = import_module("esprit.providers.config")


def _write_opencode_config(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_get_available_models_merges_supported_opencode_routes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(provider_config, "_get_cached_opencode_live_model_ids", lambda: set())
    config_path = tmp_path / "opencode.json"
    _write_opencode_config(
        config_path,
        {
            "provider": {
                "openai": {
                    "models": {
                        "gpt-5.9-preview": {"name": "GPT-5.9 Preview"},
                    }
                }
            }
        },
    )

    monkeypatch.setattr(provider_config, "get_opencode_config_path", lambda: config_path)
    models = provider_config.get_available_models()

    assert ("gpt-5.9-preview", "GPT-5.9 Preview [OpenCode route]") in models["openai"]


def test_get_available_models_maps_google_antigravity_routes(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(provider_config, "_get_cached_opencode_live_model_ids", lambda: set())
    config_path = tmp_path / "opencode.json"
    _write_opencode_config(
        config_path,
        {
            "provider": {
                "google": {
                    "models": {
                        "antigravity-gemini-3-pro": {"name": "Gemini 3 Pro (Antigravity)"},
                    }
                }
            }
        },
    )

    monkeypatch.setattr(provider_config, "get_opencode_config_path", lambda: config_path)
    models = provider_config.get_available_models()

    assert ("gemini-3-pro", "Gemini 3 Pro (Antigravity) [OpenCode route]") in models["antigravity"]
    assert all(model_id != "antigravity-gemini-3-pro" for model_id, _ in models["google"])


def test_get_available_models_ignores_invalid_opencode_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(provider_config, "_get_cached_opencode_live_model_ids", lambda: set())
    config_path = tmp_path / "opencode.json"
    config_path.write_text("{invalid json", encoding="utf-8")

    monkeypatch.setattr(provider_config, "get_opencode_config_path", lambda: config_path)
    models = provider_config.get_available_models()

    assert models["opencode"] == provider_config.AVAILABLE_MODELS["opencode"]


def test_get_available_models_merges_live_opencode_models(monkeypatch) -> None:
    monkeypatch.setattr(provider_config, "_load_opencode_route_models", dict)
    monkeypatch.setattr(
        provider_config,
        "_get_cached_opencode_live_model_ids",
        lambda: {"fresh-free-model", "gpt-5.2"},
    )

    models = provider_config.get_available_models()
    opencode_models = models["opencode"]

    assert ("fresh-free-model", "fresh-free-model [OpenCode live]") in opencode_models
    assert sum(1 for model_id, _ in opencode_models if model_id == "gpt-5.2") == 1


def test_get_public_opencode_models_includes_live_free_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        provider_config,
        "_get_cached_opencode_live_model_ids",
        lambda: {"latest-free", "latest-paid"},
    )
    public_models = provider_config.get_public_opencode_models({"opencode": []})

    assert "latest-free" in public_models
    assert "latest-paid" not in public_models


def test_get_available_models_filters_opencode_to_detected_ids(monkeypatch) -> None:
    monkeypatch.setattr(provider_config, "_load_opencode_route_models", dict)
    monkeypatch.setattr(
        provider_config,
        "_get_cached_opencode_live_model_ids",
        lambda: {"kimi-k2.5-free"},
    )

    models = provider_config.get_available_models()
    opencode_ids = {model_id for model_id, _ in models["opencode"]}

    assert "kimi-k2.5-free" in opencode_ids
    assert "gpt-5.2-codex" not in opencode_ids
