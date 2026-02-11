"""Model pricing using LiteLLM's pricing database.

Fetches per-token costs from LiteLLM's GitHub-hosted pricing JSON,
with fallback to the bundled litellm.model_cost dict. Supports tiered
pricing (e.g. Claude's 200k-token threshold) and cache token costing.
"""

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

LITELLM_PRICING_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

# Tiered pricing threshold (Claude charges more above this)
_TIERED_THRESHOLD = 200_000

# Provider prefixes to try when looking up a model
_PROVIDER_PREFIXES = [
    "anthropic/",
    "openai/",
    "gemini/",
    "azure/",
    "claude-",
]

# Aliases: map model names that don't exist in the pricing DB
# to their pricing-equivalent model.
_MODEL_ALIASES: dict[str, str] = {
    "claude-opus-4-6-thinking": "claude-opus-4-6",
    "claude-opus-4-5-thinking": "claude-opus-4-5",
    "claude-sonnet-4-5-thinking": "claude-sonnet-4-5",
    "gemini-2.5-flash-thinking": "gemini-2.5-flash",
    "gemini-2.5-flash-lite": "gemini-2.5-flash",
    "gemini-3-flash": "gemini-3-flash-preview",
    "gemini-3-pro-high": "gemini-3-pro-preview",
    "gemini-3-pro-low": "gemini-3-pro-preview",
    "gemini-3-pro-image": "gemini-3-pro-image-preview",
    # Codex models are priced the same as their base GPT model
    "gpt-5.3-codex": "gpt-5",
    "gpt-5.2-codex": "gpt-5",
    "gpt-5.1-codex": "gpt-5",
    "gpt-5.1-codex-max": "gpt-5",
    "gpt-5.1-codex-mini": "gpt-5-mini",
    "gpt-5-codex": "gpt-5",
    "gpt-5-codex-mini": "gpt-5-mini",
}

# Fields we care about from the pricing JSON
_PRICING_FIELDS = {
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_creation_input_token_cost",
    "cache_read_input_token_cost",
    "input_cost_per_token_above_200k_tokens",
    "output_cost_per_token_above_200k_tokens",
    "cache_creation_input_token_cost_above_200k_tokens",
    "cache_read_input_token_cost_above_200k_tokens",
    "max_input_tokens",
}


class ModelPricing:
    """Per-token pricing for a single model."""

    __slots__ = (
        "input_cost",
        "output_cost",
        "cache_write_cost",
        "cache_read_cost",
        "input_cost_above",
        "output_cost_above",
        "cache_write_cost_above",
        "cache_read_cost_above",
        "max_input_tokens",
    )

    def __init__(self, data: dict[str, Any]) -> None:
        self.input_cost: float = data.get("input_cost_per_token") or 0.0
        self.output_cost: float = data.get("output_cost_per_token") or 0.0
        self.cache_write_cost: float = data.get("cache_creation_input_token_cost") or 0.0
        self.cache_read_cost: float = data.get("cache_read_input_token_cost") or 0.0
        self.input_cost_above: float = data.get("input_cost_per_token_above_200k_tokens") or 0.0
        self.output_cost_above: float = data.get("output_cost_per_token_above_200k_tokens") or 0.0
        self.cache_write_cost_above: float = (
            data.get("cache_creation_input_token_cost_above_200k_tokens") or 0.0
        )
        self.cache_read_cost_above: float = (
            data.get("cache_read_input_token_cost_above_200k_tokens") or 0.0
        )
        self.max_input_tokens: int = data.get("max_input_tokens") or 0


def _tiered_cost(
    tokens: int,
    base_rate: float,
    above_rate: float,
    threshold: int = _TIERED_THRESHOLD,
) -> float:
    """Calculate cost with optional tiered pricing above a token threshold."""
    if tokens <= 0:
        return 0.0
    if tokens > threshold and above_rate > 0:
        below = min(tokens, threshold)
        above = tokens - threshold
        return below * base_rate + above * above_rate
    return tokens * base_rate


def calculate_cost(
    pricing: ModelPricing,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
) -> float:
    """Calculate total cost in USD from token counts and pricing info.

    ``cached_tokens`` are treated as cache-read tokens (prompt hits) and
    subtracted from ``input_tokens`` for the regular input cost calculation.
    """
    # Cache-read tokens are a subset of input tokens already counted
    regular_input = max(0, input_tokens - cached_tokens)

    input_cost = _tiered_cost(regular_input, pricing.input_cost, pricing.input_cost_above)
    output_cost = _tiered_cost(output_tokens, pricing.output_cost, pricing.output_cost_above)
    cache_read = _tiered_cost(cached_tokens, pricing.cache_read_cost, pricing.cache_read_cost_above)

    return input_cost + output_cost + cache_read


class PricingDB:
    """Thread-safe model pricing database backed by LiteLLM's pricing JSON."""

    def __init__(self) -> None:
        self._data: dict[str, ModelPricing] = {}
        self._loaded = False
        self._lock = threading.Lock()
        self._fetch_attempted = False

    def _load_bundled(self) -> None:
        """Load from litellm's bundled model_cost as baseline."""
        try:
            import litellm

            for name, info in litellm.model_cost.items():
                if isinstance(info, dict) and info.get("input_cost_per_token"):
                    self._data[name] = ModelPricing(info)
            self._loaded = True
            logger.debug("Loaded %d models from bundled litellm.model_cost", len(self._data))
        except Exception:
            logger.debug("Failed to load bundled litellm pricing")

    def _fetch_remote(self) -> None:
        """Fetch latest pricing from LiteLLM's GitHub repo."""
        if self._fetch_attempted:
            return
        self._fetch_attempted = True
        try:
            from urllib.request import urlopen, Request

            req = Request(LITELLM_PRICING_URL, headers={"User-Agent": "esprit"})
            with urlopen(req, timeout=10) as resp:
                raw: dict[str, Any] = json.loads(resp.read())
            count = 0
            for name, info in raw.items():
                if isinstance(info, dict) and info.get("input_cost_per_token"):
                    self._data[name] = ModelPricing(info)
                    count += 1
            logger.debug("Fetched %d models from LiteLLM remote pricing", count)
        except Exception:
            logger.debug("Failed to fetch remote pricing, using bundled data")

    def ensure_loaded(self) -> None:
        """Load pricing data (bundled first, then remote in background)."""
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            self._load_bundled()
            # Fetch remote in background to get latest prices without blocking
            t = threading.Thread(target=self._fetch_remote, daemon=True)
            t.start()

    def _resolve_model(self, model: str) -> ModelPricing | None:
        """Resolve a model name to its pricing entry."""
        # Strip provider prefix for bare name
        bare = model.split("/", 1)[-1] if "/" in model else model

        # Direct lookups
        for candidate in (model, bare):
            if candidate in self._data:
                return self._data[candidate]

        # Try with provider prefixes
        for prefix in _PROVIDER_PREFIXES:
            key = f"{prefix}{bare}"
            if key in self._data:
                return self._data[key]

        # Try alias
        alias = _MODEL_ALIASES.get(bare)
        if alias:
            return self._resolve_model(alias)

        # Fuzzy: substring match (e.g. "claude-sonnet-4-5-20250514" matches "claude-sonnet-4-5")
        bare_lower = bare.lower()
        for key, pricing in self._data.items():
            key_lower = key.lower()
            if bare_lower in key_lower or key_lower in bare_lower:
                return pricing

        return None

    def get_pricing(self, model: str) -> ModelPricing | None:
        """Get pricing for a model name. Returns None if not found."""
        self.ensure_loaded()
        return self._resolve_model(model)

    def get_cost(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
    ) -> float:
        """Calculate session cost in USD for a model and token counts."""
        pricing = self.get_pricing(model)
        if not pricing:
            return 0.0
        return calculate_cost(pricing, input_tokens, output_tokens, cached_tokens)

    def get_context_limit(self, model: str) -> int:
        """Get the max input token limit for a model."""
        pricing = self.get_pricing(model)
        if pricing and pricing.max_input_tokens > 0:
            return pricing.max_input_tokens
        return 128_000  # sensible default


# Module-level singleton
_db: PricingDB | None = None


def get_pricing_db() -> PricingDB:
    """Get or create the global pricing database."""
    global _db
    if _db is None:
        _db = PricingDB()
    return _db


# ---------------------------------------------------------------------------
# Lifetime cost tracking — persisted in ~/.esprit/usage.json
# ---------------------------------------------------------------------------

_USAGE_FILE = Path.home() / ".esprit" / "usage.json"


def _read_usage() -> dict[str, Any]:
    try:
        if _USAGE_FILE.exists():
            return json.loads(_USAGE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        pass
    return {}


def _write_usage(data: dict[str, Any]) -> None:
    try:
        _USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _USAGE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if os.name != "nt":
            _USAGE_FILE.chmod(0o600)
    except OSError:
        logger.debug("Failed to write usage file")


def get_lifetime_cost() -> float:
    """Read the accumulated lifetime cost from disk."""
    return float(_read_usage().get("lifetime_cost", 0.0))


def add_session_cost(session_cost: float) -> float:
    """Add session cost to lifetime total. Returns new lifetime total."""
    usage = _read_usage()
    lifetime = float(usage.get("lifetime_cost", 0.0)) + session_cost
    usage["lifetime_cost"] = round(lifetime, 4)
    usage["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_usage(usage)
    return lifetime
