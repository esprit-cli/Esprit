"""
Provider authentication plugins for Esprit CLI.

Supports OAuth-based authentication for:
- Anthropic Claude Pro/Max
- OpenAI ChatGPT Plus/Pro (Codex)
- GitHub Copilot
- Google Gemini
- xAI Grok
- AWS Bedrock
"""

from esprit.providers.base import ProviderAuth, AuthMethod, OAuthCredentials
from esprit.providers.anthropic_oauth import AnthropicOAuthProvider
from esprit.providers.openai_codex import OpenAICodexProvider
from esprit.providers.copilot import CopilotProvider
from esprit.providers.google_gemini import GoogleGeminiProvider
from esprit.providers.token_store import TokenStore

# Provider registry
PROVIDERS: dict[str, type[ProviderAuth]] = {
    "anthropic": AnthropicOAuthProvider,
    "openai": OpenAICodexProvider,
    "github-copilot": CopilotProvider,
    "google": GoogleGeminiProvider,
}

# Provider display names
PROVIDER_NAMES: dict[str, str] = {
    "anthropic": "Anthropic (Claude Pro/Max)",
    "openai": "OpenAI (ChatGPT Plus/Pro)",
    "github-copilot": "GitHub Copilot",
    "google": "Google (Gemini)",
}


def get_provider_auth(provider_id: str) -> ProviderAuth | None:
    """Get a provider auth instance by ID."""
    provider_class = PROVIDERS.get(provider_id)
    if provider_class:
        return provider_class()
    return None


def list_providers() -> list[str]:
    """List all available provider IDs."""
    return list(PROVIDERS.keys())


__all__ = [
    "ProviderAuth",
    "AuthMethod",
    "OAuthCredentials",
    "TokenStore",
    "PROVIDERS",
    "PROVIDER_NAMES",
    "get_provider_auth",
    "list_providers",
    "AnthropicOAuthProvider",
    "OpenAICodexProvider",
    "CopilotProvider",
    "GoogleGeminiProvider",
]
