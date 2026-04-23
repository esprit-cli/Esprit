"""Tests for LiteLLM dynamic model catalog fetching."""

import json
from importlib import import_module
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

provider_config = import_module("esprit.providers.config")


@pytest.fixture(autouse=True)
def _reset_litellm_cache():
    """Reset in-memory cache before each test."""
    provider_config._litellm_cache["expires_at"] = 0.0
    provider_config._litellm_cache["models"] = {}
    yield
    provider_config._litellm_cache["expires_at"] = 0.0
    provider_config._litellm_cache["models"] = {}


SAMPLE_CATALOG = {
    "sample_spec": {"litellm_provider": "openai", "mode": "chat"},
    "gpt-5.4": {
        "litellm_provider": "openai",
        "mode": "chat",
        "max_input_tokens": 272000,
    },
    "codex-mini-latest": {
        "litellm_provider": "openai",
        "mode": "responses",
    },
    "claude-opus-4-7": {
        "litellm_provider": "anthropic",
        "mode": "chat",
    },
    "claude-sonnet-4-6": {
        "litellm_provider": "anthropic",
        "mode": "chat",
    },
    "gemini/gemini-3-flash": {
        "litellm_provider": "gemini",
        "mode": "chat",
    },
    # Should be filtered out:
    "ft:gpt-4o-2024-08-06": {
        "litellm_provider": "openai",
        "mode": "chat",
    },
    "gpt-4o-realtime-preview": {
        "litellm_provider": "openai",
        "mode": "chat",
    },
    "gpt-4o-audio-preview": {
        "litellm_provider": "openai",
        "mode": "chat",
    },
    "1024-x-1024/dall-e-2": {
        "litellm_provider": "openai",
        "mode": "image_generation",
    },
    "text-embedding-3-small": {
        "litellm_provider": "openai",
        "mode": "embedding",
    },
    "fireworks_ai/some-model": {
        "litellm_provider": "fireworks_ai",
        "mode": "chat",
    },
    # Date-suffixed (should be filtered)
    "claude-sonnet-4-6-20260205": {
        "litellm_provider": "anthropic",
        "mode": "chat",
    },
    "gpt-5.4-2026-03-05": {
        "litellm_provider": "openai",
        "mode": "chat",
    },
    # Deprecated (should be filtered)
    "gpt-3.5-turbo": {
        "litellm_provider": "openai",
        "mode": "chat",
    },
    "gpt-4": {
        "litellm_provider": "openai",
        "mode": "chat",
    },
    # Build suffix (should be filtered)
    "gemini/gemini-2.0-flash-001": {
        "litellm_provider": "gemini",
        "mode": "chat",
    },
}


def _mock_response(status_code: int, **kwargs) -> httpx.Response:
    """Create a mock httpx.Response with a request set."""
    resp = httpx.Response(status_code, **kwargs)
    resp._request = httpx.Request("GET", "https://example.com")
    return resp


def test_fetch_litellm_catalog_success(monkeypatch):
    """Successful fetch returns filtered models grouped by Esprit provider."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    mock_response = _mock_response(200, json=SAMPLE_CATALOG)
    with patch.object(httpx, "get", return_value=mock_response):
        result = provider_config._fetch_litellm_catalog()

    # OpenAI: gpt-5.4 (chat) + codex-mini-latest (responses)
    openai_ids = {mid for mid, _ in result.get("openai", [])}
    assert "gpt-5.4" in openai_ids
    assert "codex-mini-latest" in openai_ids

    # Filtered out: ft:, realtime, audio, deprecated, date-suffixed
    assert "ft:gpt-4o-2024-08-06" not in openai_ids
    assert "gpt-4o-realtime-preview" not in openai_ids
    assert "gpt-4o-audio-preview" not in openai_ids
    assert "gpt-3.5-turbo" not in openai_ids  # deprecated
    assert "gpt-4" not in openai_ids  # deprecated
    assert "gpt-5.4-2026-03-05" not in openai_ids  # date-suffixed

    # Anthropic: keep current aliases, skip dated snapshots
    anthropic_ids = {mid for mid, _ in result.get("anthropic", [])}
    assert "claude-opus-4-7" in anthropic_ids
    assert "claude-sonnet-4-6" in anthropic_ids
    assert "claude-sonnet-4-6-20260205" not in anthropic_ids

    # Gemini → google (prefix stripped, build suffix stripped)
    google_ids = {mid for mid, _ in result.get("google", [])}
    assert "gemini-3-flash" in google_ids
    assert "gemini-2.0-flash-001" not in google_ids  # build suffix

    # Fireworks should not appear (not in provider map)
    assert "fireworks_ai" not in result

    # sample_spec should be skipped
    for models in result.values():
        assert all(mid != "sample_spec" for mid, _ in models)


def test_fetch_litellm_catalog_timeout(monkeypatch):
    """HTTP timeout returns empty dict."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    with patch.object(
        httpx, "get", side_effect=httpx.TimeoutException("timeout")
    ):
        result = provider_config._fetch_litellm_catalog()

    assert result == {}


def test_fetch_litellm_catalog_invalid_json(monkeypatch):
    """Non-JSON response returns empty dict."""
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

    mock_response = _mock_response(200, content=b"not json")
    with patch.object(httpx, "get", return_value=mock_response):
        result = provider_config._fetch_litellm_catalog()

    assert result == {}


def test_fetch_litellm_catalog_skips_in_pytest():
    """Fetch is skipped during pytest (PYTEST_CURRENT_TEST is set)."""
    # PYTEST_CURRENT_TEST is set automatically by pytest
    result = provider_config._fetch_litellm_catalog()
    assert result == {}


def test_disk_cache_write_and_read(tmp_path: Path, monkeypatch):
    """Write cache to disk, read it back."""
    cache_path = tmp_path / "models_cache.json"
    monkeypatch.setattr(
        provider_config, "_get_litellm_cache_path", lambda: cache_path
    )

    data = {
        "openai": [("gpt-5.4", "GPT 5.4")],
        "anthropic": [("claude-opus-4-7", "Claude Opus 4 7")],
    }

    provider_config._save_litellm_cache_to_disk(data)
    assert cache_path.exists()

    loaded = provider_config._load_litellm_cache_from_disk()
    assert loaded == data


def test_disk_cache_corrupt(tmp_path: Path, monkeypatch):
    """Corrupt cache file returns empty dict."""
    cache_path = tmp_path / "models_cache.json"
    cache_path.write_text("{invalid json", encoding="utf-8")
    monkeypatch.setattr(
        provider_config, "_get_litellm_cache_path", lambda: cache_path
    )

    result = provider_config._load_litellm_cache_from_disk()
    assert result == {}


def test_disk_cache_missing(tmp_path: Path, monkeypatch):
    """Missing cache file returns empty dict."""
    cache_path = tmp_path / "nonexistent" / "models_cache.json"
    monkeypatch.setattr(
        provider_config, "_get_litellm_cache_path", lambda: cache_path
    )

    result = provider_config._load_litellm_cache_from_disk()
    assert result == {}


def test_inmemory_cache_ttl(monkeypatch):
    """In-memory cache returns cached data without re-fetching."""
    cached_models = {"openai": [("gpt-5.4", "GPT 5.4")]}
    provider_config._litellm_cache["expires_at"] = (
        __import__("time").monotonic() + 9999
    )
    provider_config._litellm_cache["models"] = cached_models

    # Should return cached data without calling fetch
    result = provider_config._get_cached_litellm_models()
    assert result == cached_models


def test_get_available_models_merges_litellm(monkeypatch):
    """Dynamic LiteLLM models appear in the catalog."""
    monkeypatch.setattr(
        provider_config, "_get_cached_opencode_live_model_ids", lambda: set()
    )
    monkeypatch.setattr(provider_config, "_load_opencode_route_models", dict)

    dynamic_models = {
        "openai": [("gpt-5.4", "GPT 5.4"), ("gpt-5.4-mini", "GPT 5.4 Mini")],
    }
    monkeypatch.setattr(
        provider_config, "_get_cached_litellm_models", lambda: dynamic_models
    )

    models = provider_config.get_available_models()
    openai_ids = {mid for mid, _ in models["openai"]}

    # Dynamic models should be present
    assert "gpt-5.4" in openai_ids
    assert "gpt-5.4-mini" in openai_ids


def test_get_available_models_litellm_replaces_static(monkeypatch):
    """Dynamic models fully replace the static list for that provider."""
    monkeypatch.setattr(
        provider_config, "_get_cached_opencode_live_model_ids", lambda: set()
    )
    monkeypatch.setattr(provider_config, "_load_opencode_route_models", dict)

    dynamic_models = {
        "openai": [("gpt-5.4", "GPT 5.4")],
    }
    monkeypatch.setattr(
        provider_config, "_get_cached_litellm_models", lambda: dynamic_models
    )

    models = provider_config.get_available_models()
    openai_ids = {mid for mid, _ in models["openai"]}

    # Dynamic model should be present
    assert "gpt-5.4" in openai_ids
    # Static-only models should NOT appear (dynamic replaces entirely)
    assert "gpt-5.3-codex" not in openai_ids


def test_get_available_models_no_litellm_fallback(monkeypatch):
    """When LiteLLM returns nothing, static AVAILABLE_MODELS still work."""
    monkeypatch.setattr(
        provider_config, "_get_cached_opencode_live_model_ids", lambda: set()
    )
    monkeypatch.setattr(provider_config, "_load_opencode_route_models", dict)
    monkeypatch.setattr(
        provider_config, "_get_cached_litellm_models", lambda: {}
    )

    models = provider_config.get_available_models()

    # Should still have the hardcoded models
    assert len(models["openai"]) > 0
    assert len(models["anthropic"]) > 0


def test_model_id_to_display_name():
    """Display name generation from model IDs."""
    assert provider_config._model_id_to_display_name("gpt-5.3-codex") == "GPT 5.3 Codex"
    assert provider_config._model_id_to_display_name("claude-sonnet-4-6") == "Claude Sonnet 4 6"
    assert (
        provider_config._model_id_to_display_name("gemini/gemini-2.5-flash")
        == "Gemini 2.5 Flash"
    )
    assert provider_config._model_id_to_display_name("o3-mini") == "O3 Mini"
    assert provider_config._model_id_to_display_name("gpt-5") == "GPT 5"


def test_cached_litellm_models_falls_to_disk(tmp_path: Path, monkeypatch):
    """When fetch fails, disk cache is used."""
    cache_path = tmp_path / "models_cache.json"
    disk_data = {"anthropic": [("claude-opus-4-7", "Claude Opus 4 7")]}
    cache_path.write_text(json.dumps(disk_data), encoding="utf-8")

    monkeypatch.setattr(
        provider_config, "_get_litellm_cache_path", lambda: cache_path
    )
    monkeypatch.setattr(
        provider_config, "_fetch_litellm_catalog", lambda **kw: {}
    )

    result = provider_config._get_cached_litellm_models()
    assert result == disk_data
