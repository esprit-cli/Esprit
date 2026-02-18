"""
OpenRouter API key provider for Esprit.

Simple API-key-based provider for accessing models via OpenRouter.
No OAuth — just stores and retrieves the user's API key.
"""

from typing import Any

from esprit.providers.base import (
    AuthCallbackResult,
    AuthMethod,
    AuthorizationResult,
    OAuthCredentials,
    ProviderAuth,
)


class OpenRouterProvider(ProviderAuth):
    """OpenRouter provider — API key authentication only."""

    provider_id = "openrouter"
    display_name = "OpenRouter"

    async def authorize(self, **kwargs: Any) -> AuthorizationResult:
        return AuthorizationResult(
            url="https://openrouter.ai/keys",
            instructions="Paste your OpenRouter API key.",
            method=AuthMethod.CODE,
        )

    async def callback(
        self, auth_result: AuthorizationResult, code: str | None = None
    ) -> AuthCallbackResult:
        if not code or not code.strip():
            return AuthCallbackResult(success=False, error="No API key provided")

        api_key = code.strip()
        credentials = OAuthCredentials(
            type="api",
            access_token=api_key,
        )
        return AuthCallbackResult(success=True, credentials=credentials)

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        # API keys don't expire
        return credentials

    def modify_request(
        self,
        url: str,
        headers: dict[str, str],
        body: Any,
        credentials: OAuthCredentials,
    ) -> tuple[str, dict[str, str], Any]:
        modified = dict(headers)
        modified["Authorization"] = f"Bearer {credentials.access_token}"
        modified["HTTP-Referer"] = "https://esprit.dev"
        modified["X-Title"] = "Esprit"
        return url, modified, body

    def get_auth_methods(self) -> list[dict[str, str]]:
        return [
            {"type": "api", "label": "Enter OpenRouter API Key"},
        ]
