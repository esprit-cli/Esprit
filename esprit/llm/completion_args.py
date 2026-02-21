from typing import Any

from esprit.config import Config

try:
    from esprit.providers.litellm_integration import (
        get_provider_api_key,
        get_provider_headers,
        should_use_oauth,
    )

    PROVIDERS_AVAILABLE = True
except ImportError:
    PROVIDERS_AVAILABLE = False


class CompletionArgsError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def build_completion_args(
    model_name: str,
    messages: list[dict[str, Any]],
    timeout: int,
    tools: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "model": model_name,
        "messages": messages,
        "timeout": timeout,
    }

    if tools:
        args["tools"] = tools

    # Translate google/ -> gemini/ for litellm compatibility.
    if model_name.lower().startswith("google/"):
        args["model"] = "gemini/" + model_name.split("/", 1)[1]

    # Esprit subscription provider routes via Esprit's OpenAI-compatible proxy.
    if model_name.lower().startswith("esprit/"):
        if not PROVIDERS_AVAILABLE:
            raise CompletionArgsError(
                "Provider integrations unavailable. Reinstall esprit-cli with provider support.",
                status_code=500,
            )

        from esprit.providers.esprit_subs import (
            LLM_PROXY_URL,
            _load_esprit_credentials,
            resolve_bedrock_model,
        )

        bare_model = model_name.split("/", 1)[1]
        bedrock_model = resolve_bedrock_model(bare_model)
        creds = _load_esprit_credentials()
        if not creds or not creds.access_token:
            raise CompletionArgsError(
                "Not logged in to Esprit. Run 'esprit provider login esprit' first.",
                status_code=401,
            )

        args["model"] = f"openai/{bedrock_model}"
        args["api_base"] = LLM_PROXY_URL
        args["api_key"] = creds.access_token
        args["extra_headers"] = {
            "X-Esprit-Provider": "bedrock",
            "X-Esprit-Model": bedrock_model,
        }
        return args

    use_oauth = False
    provider_api_key: str | None = None
    if PROVIDERS_AVAILABLE:
        provider_api_key = get_provider_api_key(model_name)
        if provider_api_key:
            args["api_key"] = provider_api_key

        use_oauth = should_use_oauth(model_name)
        if use_oauth:
            model_lower = model_name.lower()
            if "codex" in model_lower:
                bare_model = model_name.split("/", 1)[-1]
                args["model"] = bare_model
                args["api_key"] = provider_api_key or "oauth-auth"
            else:
                provider_headers = get_provider_headers(model_name)
                if provider_headers:
                    args["extra_headers"] = provider_headers
                args["api_key"] = provider_api_key or "oauth-auth"

    if not use_oauth:
        if "api_key" not in args and (api_key := Config.get("llm_api_key")):
            args["api_key"] = api_key
        if api_base := (
            Config.get("llm_api_base")
            or Config.get("openai_api_base")
            or Config.get("litellm_base_url")
            or Config.get("ollama_api_base")
        ):
            args["api_base"] = api_base

    return args
