"""Model-name normalization utilities for LiteLLM routing."""

from __future__ import annotations

# OpenAI Codex aliases accepted by Esprit.
OPENAI_CODEX_MODEL_ALIASES: dict[str, str] = {
    "codex-5.3": "gpt-5.3-codex",
    "codex-5.2": "gpt-5.2-codex",
    "codex-5.1": "gpt-5.1-codex",
    "codex-5": "gpt-5-codex",
}


def normalize_openai_codex_model_name(model_name: str | None) -> str | None:
    """Normalize OpenAI Codex aliases to canonical LiteLLM model IDs."""
    if not model_name:
        return model_name

    model = model_name.strip()
    if not model:
        return model_name

    raw_prefix = ""
    bare = model
    if "/" in model:
        raw_prefix, bare = model.split("/", 1)
        if raw_prefix.lower() not in {"openai", "codex"}:
            return model

    bare_lower = bare.lower()
    mapped_bare = OPENAI_CODEX_MODEL_ALIASES.get(bare_lower, bare)

    if bare_lower.startswith("codex-") or raw_prefix.lower() in {"openai", "codex"}:
        return f"openai/{mapped_bare}"

    return model


def to_litellm_model_name(model_name: str | None) -> str | None:
    """Convert a configured model into the provider prefix LiteLLM expects."""
    if not model_name:
        return model_name

    normalized = normalize_openai_codex_model_name(model_name) or model_name
    model = normalized.strip()
    if not model:
        return normalized

    lower_model = model.lower()
    if lower_model.startswith("google/"):
        return "gemini/" + model.split("/", 1)[1]
    if lower_model.startswith(("opencode/", "zen/")):
        return "openai/" + model.split("/", 1)[1]
    return model
