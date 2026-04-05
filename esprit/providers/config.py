"""
Configuration management for Esprit CLI.

Stores user preferences like default model, etc.
"""

import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

console = Console()
logger = logging.getLogger(__name__)

# Available models by provider
AVAILABLE_MODELS = {
    "esprit": [
        ("default", "Esprit Default"),
        ("kimi-k2.5", "Esprit Pro"),
        ("haiku", "Esprit Fast"),
    ],
    "openai": [
        ("gpt-5.3-codex", "GPT-5.3 Codex (recommended)"),
        ("gpt-5.1-codex", "GPT-5.1 Codex"),
        ("gpt-5.1-codex-max", "GPT-5.1 Codex Max (maximum context)"),
        ("gpt-5.1-codex-mini", "GPT-5.1 Codex Mini (faster)"),
        ("codex-mini-latest", "Codex Mini (faster, lightweight)"),
        ("gpt-5.2", "GPT-5.2"),
        ("gpt-5.2-codex", "GPT-5.2 Codex"),
    ],
    "anthropic": [
        ("claude-sonnet-4-5-20250514", "Claude Sonnet 4.5 (recommended)"),
        ("claude-opus-4-5-20251101", "Claude Opus 4.5 (advanced reasoning)"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5 (faster)"),
    ],
    "github-copilot": [
        ("gpt-5", "GPT-5 (via Copilot)"),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5 (via Copilot)"),
    ],
    "google": [
        ("gemini-3-pro", "Gemini 3 Pro (recommended)"),
        ("gemini-3-flash", "Gemini 3 Flash (faster)"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ],
    "opencode": [
        ("gpt-5.2-codex", "GPT-5.2 Codex (recommended)"),
        ("gpt-5.1-codex", "GPT-5.1 Codex"),
        ("gpt-5.1-codex-max", "GPT-5.1 Codex Max"),
        ("gpt-5.1-codex-mini", "GPT-5.1 Codex Mini"),
        ("gpt-5-codex", "GPT-5 Codex"),
        ("gpt-5.2", "GPT-5.2"),
        ("gpt-5.1", "GPT-5.1"),
        ("gpt-5", "GPT-5"),
        ("gpt-5-nano", "GPT-5 Nano"),
        ("claude-opus-4-6", "Claude Opus 4.6"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-opus-4-5", "Claude Opus 4.5"),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
        ("claude-opus-4-1", "Claude Opus 4.1"),
        ("claude-sonnet-4", "Claude Sonnet 4"),
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
        ("claude-3-5-haiku", "Claude Haiku 3.5"),
        ("gemini-3.1-pro", "Gemini 3.1 Pro"),
        ("gemini-3-pro", "Gemini 3 Pro"),
        ("gemini-3-flash", "Gemini 3 Flash"),
        ("kimi-k2.5", "Kimi K2.5"),
        ("kimi-k2-thinking", "Kimi K2 Thinking"),
        ("kimi-k2", "Kimi K2"),
        ("qwen3-coder", "Qwen3 Coder"),
        ("glm-5", "GLM-5"),
        ("glm-4.7", "GLM-4.7"),
        ("glm-4.6", "GLM-4.6"),
        ("minimax-m2.5", "MiniMax M2.5"),
        ("minimax-m2.1", "MiniMax M2.1"),
        ("grok-code", "Grok Code Fast 1"),
        ("big-pickle", "Big Pickle"),
        ("glm-5-free", "GLM-5 Free"),
        ("glm-4.7-free", "GLM-4.7 Free"),
        ("kimi-k2.5-free", "Kimi K2.5 Free"),
        ("minimax-m2.1-free", "MiniMax M2.1 Free"),
        ("minimax-m2.5-free", "MiniMax M2.5 Free"),
        ("trinity-large-preview-free", "Trinity Large Preview Free"),
    ],
    "antigravity": [
        ("claude-opus-4-6-thinking", "Claude Opus 4.6 Thinking (free)"),
        ("claude-opus-4-5-thinking", "Claude Opus 4.5 Thinking (free)"),
        ("claude-sonnet-4-5-thinking", "Claude Sonnet 4.5 Thinking (free)"),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5 (free)"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash (free)"),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite (free)"),
        ("gemini-2.5-flash-thinking", "Gemini 2.5 Flash Thinking (free)"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro (free)"),
        ("gemini-3-flash", "Gemini 3 Flash (free)"),
        ("gemini-3-pro-high", "Gemini 3 Pro High (free)"),
        ("gemini-3-pro-image", "Gemini 3 Pro Image (free)"),
        ("gemini-3-pro-low", "Gemini 3 Pro Low (free)"),
    ],
}

# Public OpenCode models available without explicit OpenCode credentials.
# Keep this list conservative: models with explicit "free" markers plus
# known no-auth models observed in OpenCode CLI.
_OPENCODE_ALWAYS_PUBLIC_MODELS: frozenset[str] = frozenset(
    {
        "gpt-5-nano",
        "big-pickle",
    }
)

_OPENCODE_PROVIDER_ALIASES: dict[str, str] = {
    "zen": "opencode",
    "codex": "openai",
}

_OPENCODE_MODELS_CACHE_TTL_SECONDS = 300.0
_opencode_models_cache: dict[str, Any] = {
    "expires_at": 0.0,
    "model_ids": frozenset(),
}


def _opencode_models_endpoint() -> str:
    base = (
        os.environ.get("OPENCODE_BASE_URL")
        or os.environ.get("OPENCODE_API_BASE")
        or "https://opencode.ai/zen/v1"
    )
    return f"{base.rstrip('/')}/models"


def _fetch_opencode_live_model_ids(timeout_seconds: float = 2.0) -> set[str]:
    """Fetch live OpenCode model IDs from the OpenAI-compatible /models endpoint."""
    # Avoid network during unit tests unless explicitly requested.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return set()

    try:
        response = httpx.get(_opencode_models_endpoint(), timeout=timeout_seconds)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return set()

    data = payload.get("data")
    if not isinstance(data, list):
        return set()

    model_ids: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_model_id = item.get("id")
        if isinstance(raw_model_id, str) and raw_model_id.strip():
            model_ids.add(raw_model_id.strip().lower())

    return model_ids


def _get_cached_opencode_live_model_ids() -> set[str]:
    """Return cached live OpenCode model IDs, refreshing periodically."""
    now = time.monotonic()
    if _opencode_models_cache["expires_at"] > now:
        return set(_opencode_models_cache["model_ids"])

    model_ids = _fetch_opencode_live_model_ids()
    _opencode_models_cache["expires_at"] = now + _OPENCODE_MODELS_CACHE_TTL_SECONDS
    _opencode_models_cache["model_ids"] = frozenset(model_ids)
    return model_ids


def get_opencode_config_path() -> Path:
    """Resolve opencode.json path following XDG_CONFIG_HOME."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "opencode" / "opencode.json"
    return Path.home() / ".config" / "opencode" / "opencode.json"


def _load_opencode_route_models() -> dict[str, list[tuple[str, str]]]:
    """Load provider/model routes from OpenCode config for menu parity."""
    config_path = get_opencode_config_path()
    if not config_path.exists():
        return {}

    try:
        with config_path.open(encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    providers = config.get("provider")
    if not isinstance(providers, dict):
        return {}

    routes: dict[str, list[tuple[str, str]]] = {}

    for raw_provider_id, provider_config in providers.items():
        if not isinstance(raw_provider_id, str) or not isinstance(provider_config, dict):
            continue
        raw_models = provider_config.get("models")
        if not isinstance(raw_models, dict):
            continue

        provider_id = _OPENCODE_PROVIDER_ALIASES.get(raw_provider_id.strip().lower(), raw_provider_id.strip().lower())

        for raw_model_id, model_config in raw_models.items():
            if not isinstance(raw_model_id, str):
                continue
            model_id = raw_model_id.strip()
            if not model_id:
                continue

            display_name = model_id
            if isinstance(model_config, dict):
                raw_name = model_config.get("name")
                if isinstance(raw_name, str) and raw_name.strip():
                    display_name = raw_name.strip()

            target_provider = provider_id
            target_model = model_id

            # OpenCode reroutes Antigravity models through a google provider section.
            # Expose these under Esprit's Antigravity provider IDs for runtime parity.
            if target_provider == "google" and target_model.startswith("antigravity-"):
                target_provider = "antigravity"
                target_model = target_model.removeprefix("antigravity-")

            if target_provider not in AVAILABLE_MODELS:
                continue

            routes.setdefault(target_provider, []).append((target_model, f"{display_name} [OpenCode route]"))

    return routes


# ---------------------------------------------------------------------------
# LiteLLM dynamic model catalog
# ---------------------------------------------------------------------------

LITELLM_CATALOG_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

_LITELLM_CACHE_TTL_SECONDS = 300.0  # 5 minutes
_litellm_cache: dict[str, Any] = {
    "expires_at": 0.0,
    "models": {},
}

# Map LiteLLM provider names → Esprit provider IDs
_LITELLM_PROVIDER_MAP: dict[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "google",
    "github_copilot": "github-copilot",
}

# Model ID prefixes / substrings to skip even when mode matches
_LITELLM_SKIP_PREFIXES = ("ft:",)
_LITELLM_SKIP_SUBSTRINGS = ("realtime", "audio", "tts", "whisper", "embedding")

# Date suffix patterns to strip dated snapshot models (e.g. -20250514, -2025-04-14)
_DATE_SUFFIX_RE = re.compile(r"-\d{4}-?\d{2}-?\d{2}$")

# Deprecated / legacy model families to exclude entirely
_DEPRECATED_PREFIXES = ("gpt-3.5", "gpt-4-0", "gpt-4-1")
_DEPRECATED_MODELS = frozenset({
    "gpt-4", "gpt-4-turbo", "gpt-4-turbo-preview",
})

# Niche / experimental models to skip
_LITELLM_SKIP_NICHE_SUBSTRINGS = (
    "gemma", "learnlm", "lyria", "robotics", "container",
    "deep-research", "exp-", "computer-use", "customtools",
    "search-preview", "search-api", "-chat-latest", "chatgpt-",
    "-live-preview", "-preview-0", "-4-o-preview",
)

# Numeric build suffixes to drop (e.g. gemini-2.0-flash-001)
_BUILD_SUFFIX_RE = re.compile(r"-\d{3}$")


def _model_id_to_display_name(model_id: str) -> str:
    """Generate a human-readable display name from a model ID.

    Examples:
        gpt-5.3-codex       → GPT 5.3 Codex
        claude-sonnet-4-6    → Claude Sonnet 4 6
        gemini/gemini-2.5-flash → Gemini 2.5 Flash
    """
    # Strip provider prefix (e.g. "gemini/gemini-2.5-flash" → "gemini-2.5-flash")
    bare = model_id.split("/")[-1] if "/" in model_id else model_id

    # Split on hyphens, title-case each part
    parts = bare.split("-")
    result: list[str] = []
    for part in parts:
        # Keep version-like tokens (e.g. "4.1", "2.5") as-is
        if re.match(r"^\d+(\.\d+)*$", part):
            result.append(part)
        # Uppercase known acronyms
        elif part.lower() in {"gpt", "o1", "o3", "o4", "xl", "api"}:
            result.append(part.upper())
        else:
            result.append(part.capitalize())

    return " ".join(result)


def _fetch_litellm_catalog(
    timeout_seconds: float = 2.0,
) -> dict[str, list[tuple[str, str]]]:
    """Fetch models from LiteLLM's public catalog and filter to relevant providers."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return {}

    try:
        response = httpx.get(LITELLM_CATALOG_URL, timeout=timeout_seconds)
        response.raise_for_status()
        raw = response.json()
    except (httpx.HTTPError, ValueError, TypeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    result: dict[str, list[tuple[str, str]]] = {}

    for model_id, info in raw.items():
        if model_id == "sample_spec" or not isinstance(info, dict):
            continue

        provider = info.get("litellm_provider", "")
        esprit_provider = _LITELLM_PROVIDER_MAP.get(provider)
        if not esprit_provider:
            continue

        mode = info.get("mode", "")
        if mode not in ("chat", "responses"):
            continue

        # Skip fine-tuned, realtime, audio, etc.
        model_lower = model_id.lower()
        if any(model_lower.startswith(p) for p in _LITELLM_SKIP_PREFIXES):
            continue
        if any(s in model_lower for s in _LITELLM_SKIP_SUBSTRINGS):
            continue
        # Skip image-size-prefixed entries (e.g. "1024-x-1024/dall-e-2")
        if re.match(r"^\d+-x-\d+/", model_id):
            continue

        # For gemini models, strip the "gemini/" prefix for the stored model ID
        bare_model_id = model_id.split("/", 1)[-1] if "/" in model_id else model_id
        bare_lower = bare_model_id.lower()

        # Skip deprecated model families
        if bare_model_id in _DEPRECATED_MODELS:
            continue
        if any(bare_lower.startswith(p) for p in _DEPRECATED_PREFIXES):
            continue

        # Skip niche / experimental models
        if any(s in bare_lower for s in _LITELLM_SKIP_NICHE_SUBSTRINGS):
            continue

        # Skip date-suffixed snapshots (e.g. claude-sonnet-4-5-20250929,
        # gpt-5-2025-08-07).  Keep only the alias without the date.
        if _DATE_SUFFIX_RE.search(bare_model_id):
            continue

        # Skip build-number suffixes (e.g. gemini-2.0-flash-001)
        if _BUILD_SUFFIX_RE.search(bare_model_id):
            continue

        display_name = _model_id_to_display_name(model_id)
        result.setdefault(esprit_provider, []).append((bare_model_id, display_name))

    # Deduplicate and sort newest-first within each provider
    for prov in result:
        seen: set[str] = set()
        deduped: list[tuple[str, str]] = []
        for mid, mname in result[prov]:
            if mid not in seen:
                seen.add(mid)
                deduped.append((mid, mname))
        result[prov] = sorted(deduped, key=lambda m: _model_sort_key(m[0]), reverse=True)

    return result


def _model_sort_key(model_id: str) -> tuple[float, str]:
    """Extract a sortable version number from a model ID.

    Returns (version_number, variant) so higher versions sort first.
    Examples:
        gpt-5.4       → (5.4, "")
        gpt-5.4-mini  → (5.4, "mini")
        claude-opus-4-6 → (4.6, "opus")
        o3-mini       → (3.0, "mini")
        codex-mini-latest → (0.0, "codex-mini-latest")
    """
    bare = model_id.lower()

    # GPT models: extract version after "gpt-"
    m = re.search(r"gpt-(\d+(?:\.\d+)?)", bare)
    if m:
        return (float(m.group(1)), bare)

    # Claude models: extract major-minor from "claude-{role}-{major}-{minor}"
    m = re.search(r"claude-\w+-(\d+)-(\d+)", bare)
    if m:
        return (float(f"{m.group(1)}.{m.group(2)}"), bare)
    m = re.search(r"claude-\w+-(\d+)", bare)
    if m:
        return (float(m.group(1)), bare)

    # O-series: o1, o3, o4
    m = re.match(r"o(\d+)", bare)
    if m:
        return (float(m.group(1)), bare)

    # Gemini models: extract version after "gemini-"
    m = re.search(r"gemini-(\d+(?:\.\d+)?)", bare)
    if m:
        return (float(m.group(1)), bare)

    return (0.0, bare)


def _get_litellm_cache_path() -> Path:
    return Path.home() / ".esprit" / "models_cache.json"


def _save_litellm_cache_to_disk(
    data: dict[str, list[tuple[str, str]]],
) -> None:
    """Atomically persist the filtered catalog to disk."""
    cache_path = _get_litellm_cache_path()
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            dir=str(cache_path.parent), suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp_path, str(cache_path))
        except Exception:
            # Clean up temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:
        logger.debug("Failed to write LiteLLM model cache to disk", exc_info=True)


def _load_litellm_cache_from_disk() -> dict[str, list[tuple[str, str]]]:
    """Load cached catalog from disk."""
    cache_path = _get_litellm_cache_path()
    if not cache_path.exists():
        return {}
    try:
        with cache_path.open(encoding="utf-8") as f:
            raw = json.load(f)
        # Convert lists back to list[tuple[str, str]]
        return {
            provider: [(m[0], m[1]) for m in models]
            for provider, models in raw.items()
            if isinstance(models, list)
        }
    except (json.JSONDecodeError, OSError, IndexError, TypeError):
        return {}


def _get_cached_litellm_models() -> dict[str, list[tuple[str, str]]]:
    """Return cached LiteLLM models, refreshing periodically."""
    now = time.monotonic()
    if _litellm_cache["expires_at"] > now and _litellm_cache["models"]:
        return dict(_litellm_cache["models"])

    models = _fetch_litellm_catalog()
    if models:
        _litellm_cache["expires_at"] = now + _LITELLM_CACHE_TTL_SECONDS
        _litellm_cache["models"] = models
        _save_litellm_cache_to_disk(models)
        return dict(models)

    # Network failed — try disk cache
    disk_models = _load_litellm_cache_from_disk()
    if disk_models:
        _litellm_cache["expires_at"] = now + _LITELLM_CACHE_TTL_SECONDS
        _litellm_cache["models"] = disk_models
        return dict(disk_models)

    return {}


def get_available_models() -> dict[str, list[tuple[str, str]]]:
    """Get model catalog, merged with compatible OpenCode route definitions."""
    merged: dict[str, list[tuple[str, str]]] = {
        provider_id: list(models)
        for provider_id, models in AVAILABLE_MODELS.items()
    }

    route_models = _load_opencode_route_models()
    for provider_id, models in route_models.items():
        existing_ids = {model_id for model_id, _ in merged.get(provider_id, [])}
        for model_id, model_name in models:
            if model_id in existing_ids:
                continue
            merged.setdefault(provider_id, []).append((model_id, model_name))
            existing_ids.add(model_id)

    # Keep OpenCode catalog fresh with live IDs exposed by /models.
    live_opencode_model_ids = _get_cached_opencode_live_model_ids()
    if live_opencode_model_ids:
        existing_ids = {model_id for model_id, _ in merged.get("opencode", [])}
        for model_id in sorted(live_opencode_model_ids):
            if model_id in existing_ids:
                continue
            merged.setdefault("opencode", []).append((model_id, f"{model_id} [OpenCode live]"))
            existing_ids.add(model_id)

    # When OpenCode models are detectable on this machine (local route config
    # and/or live /models), only show those IDs in model selectors. This keeps
    # options aligned with what's actually available to the user.
    detected_opencode_model_ids = {
        model_id
        for model_id, _ in route_models.get("opencode", [])
    }
    detected_opencode_model_ids.update(live_opencode_model_ids)
    if detected_opencode_model_ids:
        merged["opencode"] = [
            (model_id, model_name)
            for model_id, model_name in merged.get("opencode", [])
            if model_id in detected_opencode_model_ids
        ]

    # Merge dynamically fetched LiteLLM models (openai, anthropic, google,
    # github-copilot).  When dynamic models are available for a provider,
    # they fully replace the hardcoded list so the catalog stays current
    # and doesn't show duplicates.
    litellm_models = _get_cached_litellm_models()
    for provider_id, models in litellm_models.items():
        if not models:
            continue
        merged[provider_id] = list(models)

    return merged


def get_public_opencode_models(
    models_by_provider: dict[str, list[tuple[str, str]]] | None = None,
) -> set[str]:
    """Return OpenCode model IDs that can be used without provider login."""
    catalog = models_by_provider or get_available_models()
    public_models = set(_OPENCODE_ALWAYS_PUBLIC_MODELS)

    for model_id, model_name in catalog.get("opencode", []):
        if model_id.endswith("-free") or "free" in model_name.lower():
            public_models.add(model_id)

    for model_id in _get_cached_opencode_live_model_ids():
        if model_id.endswith("-free"):
            public_models.add(model_id)

    return public_models


def has_public_opencode_models(
    models_by_provider: dict[str, list[tuple[str, str]]] | None = None,
) -> bool:
    """Check whether at least one public OpenCode model is available."""
    return bool(get_public_opencode_models(models_by_provider))


def is_public_opencode_model(
    model_name: str | None,
    models_by_provider: dict[str, list[tuple[str, str]]] | None = None,
) -> bool:
    """Check if model is an OpenCode model that supports no-auth access."""
    if not model_name:
        return False
    model_lower = model_name.strip().lower()
    if "/" not in model_lower:
        return False
    provider_id, bare_model = model_lower.split("/", 1)
    if provider_id not in {"opencode", "zen"}:
        return False
    return bare_model in get_public_opencode_models(models_by_provider)


class Config:
    """Configuration storage for Esprit CLI."""

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or Path.home() / ".esprit"
        self.config_file = self.config_dir / "config.json"

    def _ensure_dir(self) -> None:
        """Ensure the config directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        """Load configuration."""
        if not self.config_file.exists():
            return {}
        try:
            with self.config_file.open(encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        """Save configuration."""
        self._ensure_dir()
        with self.config_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        data = self._load()
        return data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value."""
        data = self._load()
        data[key] = value
        self._save(data)

    def get_model(self) -> str | None:
        """Get the configured model."""
        # Environment variable takes precedence
        env_model = os.getenv("ESPRIT_LLM")
        if env_model:
            return env_model
        return self.get("model")

    def set_model(self, model: str) -> None:
        """Set the default model."""
        self.set("model", model)


def get_config() -> Config:
    """Get the global config instance."""
    return Config()


_PROVIDER_LABELS: dict[str, str] = {
    "esprit": "ESPRIT (YOUR SUBSCRIPTION)",
    "antigravity": "ANTIGRAVITY",
    "openai": "OPENAI",
    "anthropic": "ANTHROPIC",
    "google": "GOOGLE",
    "opencode": "OPENCODE ZEN",
    "github-copilot": "GITHUB COPILOT",
}


def cmd_config_model(model: str | None = None) -> int:
    """Configure the default LLM model."""
    from esprit.providers.token_store import TokenStore
    from esprit.providers.account_pool import get_account_pool

    token_store = TokenStore()
    pool = get_account_pool()
    config = Config()
    models_by_provider = get_available_models()
    public_opencode_models = get_public_opencode_models(models_by_provider)

    from esprit.providers.constants import MULTI_ACCOUNT_PROVIDERS as _multi_account

    # If no model specified, show interactive menu
    if not model:
        console.print()
        console.print("[bold]Select a model to use:[/]")
        console.print()

        # Group by provider — show connected first, then disconnected
        available_options = []
        option_num = 1
        connected_providers = []
        disconnected_providers = []

        for provider_id, models in models_by_provider.items():
            if provider_id in _multi_account:
                has_creds = pool.has_accounts(provider_id)
            elif provider_id == "esprit":
                # Esprit subscription uses platform credentials, not token store
                from esprit.auth.credentials import is_authenticated
                has_creds = is_authenticated()
            elif provider_id == "opencode":
                has_creds = token_store.has_credentials(provider_id) or bool(public_opencode_models)
            else:
                has_creds = token_store.has_credentials(provider_id)
            if has_creds:
                connected_providers.append((provider_id, models))
            else:
                disconnected_providers.append((provider_id, models))

        # Show connected providers first
        for provider_id, models in connected_providers:
            if provider_id == "esprit":
                auth_type = "PLATFORM"
            elif provider_id == "opencode":
                creds = token_store.get(provider_id)
                auth_type = creds.type.upper() if creds else "PUBLIC"
            else:
                creds = token_store.get(provider_id)
                auth_type = creds.type.upper() if creds else "OAUTH"
            provider_label = _PROVIDER_LABELS.get(provider_id, provider_id.upper())
            connection_hint = f"{auth_type} connected" if auth_type != "PUBLIC" else "PUBLIC no-auth"
            console.print(f"  [bold green]●[/] [bold cyan]{provider_label}[/] [dim]({connection_hint})[/]")
            models_to_show = models
            if provider_id == "opencode" and not token_store.has_credentials("opencode"):
                models_to_show = [
                    (model_id, model_name)
                    for model_id, model_name in models
                    if model_id in public_opencode_models
                ]
            for model_id, model_name in models_to_show:
                full_model = f"{provider_id}/{model_id}"
                available_options.append(full_model)
                console.print(f"    [bold]{option_num}.[/] {model_name} [dim]({model_id})[/]")
                option_num += 1
            if models_to_show:
                console.print()

        # Show disconnected providers (greyed out)
        if disconnected_providers:
            for provider_id, models in disconnected_providers:
                provider_label = {
                    "esprit": "ESPRIT (YOUR SUBSCRIPTION)",
                    "antigravity": "ANTIGRAVITY",
                    "openai": "OPENAI",
                    "anthropic": "ANTHROPIC",
                    "google": "GOOGLE",
                    "opencode": "OPENCODE ZEN",
                    "github-copilot": "GITHUB COPILOT",
                }.get(provider_id, provider_id.upper())
                console.print(f"  [dim]○ {provider_label} (not connected)[/]")
                for model_id, model_name in models:
                    console.print(f"    [dim]  {model_name}[/]")
            console.print()

        if not available_options:
            console.print("[yellow]No providers configured.[/]")
            console.print()
            console.print("Run 'esprit provider login' to authenticate with a provider.")
            console.print()
            return 1

        choice = Prompt.ask(
            "Enter number",
            choices=[str(i) for i in range(1, len(available_options) + 1)],
        )
        model = available_options[int(choice) - 1]

    # Validate model format
    if "/" not in model:
        # Try to infer provider
        for provider_id, models in models_by_provider.items():
            for model_id, _ in models:
                if model_id == model:
                    model = f"{provider_id}/{model_id}"
                    break

    config.set_model(model)

    console.print()
    console.print(f"[green]✓ Default model set to: {model}[/]")
    console.print()
    console.print("[dim]This will be used when running 'esprit local'[/]")
    console.print("[dim]Override with ESPRIT_LLM environment variable[/]")
    console.print()

    return 0


def cmd_config_show() -> int:
    """Show current configuration."""
    config = Config()

    console.print()
    console.print("[bold]Current Configuration[/]")
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_column("Source")

    # Model
    env_model = os.getenv("ESPRIT_LLM")
    config_model = config.get("model")
    if env_model:
        table.add_row("Model", env_model, "[cyan]ESPRIT_LLM env[/]")
    elif config_model:
        table.add_row("Model", config_model, "[dim]~/.esprit/config.json[/]")
    else:
        from esprit.llm.config import DEFAULT_MODEL
        default_display = DEFAULT_MODEL.replace("bedrock/", "").replace("us.anthropic.", "anthropic/")
        table.add_row("Model", f"[dim]{default_display} (default)[/]", "[dim]default[/]")

    console.print(table)
    console.print()

    return 0
