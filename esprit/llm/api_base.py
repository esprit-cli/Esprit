import os

from esprit.config import Config

_PROVIDER_DEFAULT_API_BASE: dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
    "opencode": "https://opencode.ai/zen/v1",
    "zen": "https://opencode.ai/zen/v1",
}

_PROVIDER_ENV_BASE_VARS: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_URL"),
    "opencode": ("OPENCODE_BASE_URL", "OPENCODE_API_BASE"),
    "zen": ("OPENCODE_BASE_URL", "OPENCODE_API_BASE"),
}


def configured_api_base() -> str | None:
    return (
        Config.get("llm_api_base")
        or Config.get("openai_api_base")
        or Config.get("litellm_base_url")
        or Config.get("ollama_api_base")
    )


def _provider_prefix(model_name: str | None) -> str:
    if not model_name:
        return ""
    model_lower = model_name.lower()
    if "/" in model_lower:
        return model_lower.split("/", 1)[0]
    return ""


def resolve_api_base(model_name: str | None) -> str | None:
    # Explicit CLI config always wins.
    explicit = configured_api_base()
    if explicit:
        return explicit

    provider = _provider_prefix(model_name)
    return _PROVIDER_DEFAULT_API_BASE.get(provider)


def detect_conflicting_provider_base_env(model_name: str | None) -> tuple[str, str] | None:
    provider = _provider_prefix(model_name)
    for var in _PROVIDER_ENV_BASE_VARS.get(provider, ()):
        value = os.getenv(var)
        if value:
            return var, value
    return None
