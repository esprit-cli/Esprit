"""
OpenCode Zen API key provider.

OpenCode Zen exposes an OpenAI-compatible API at https://opencode.ai/zen/v1
and authenticates with an API key from https://opencode.ai/auth.
"""

from typing import Any

from esprit.providers.base import (
    AuthCallbackResult,
    AuthMethod,
    AuthorizationResult,
    OAuthCredentials,
    ProviderAuth,
)

OPENCODE_ZEN_AUTH_URL = "https://opencode.ai/auth"


class OpenCodeZenProvider(ProviderAuth):
    """OpenCode Zen provider (API key based)."""

    provider_id = "opencode"
    display_name = "OpenCode Zen"

    async def authorize(self, **_kwargs) -> AuthorizationResult:
        """Start API key collection flow."""
        return AuthorizationResult(
            url=OPENCODE_ZEN_AUTH_URL,
            instructions="Create or copy your OpenCode API key and paste it here.",
            method=AuthMethod.CODE,
        )

    async def callback(
        self,
        _auth_result: AuthorizationResult,
        code: str | None = None,
    ) -> AuthCallbackResult:
        """Treat pasted code as API key."""
        api_key = (code or "").strip()
        if not api_key:
            return AuthCallbackResult(success=False, error="API key is required")

        return AuthCallbackResult(
            success=True,
            credentials=OAuthCredentials(type="api", access_token=api_key),
        )

    async def refresh_token(
        self,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """API keys do not require refresh."""
        return credentials

    def modify_request(
        self,
        url: str,
        headers: dict[str, str],
        body: Any,
        credentials: OAuthCredentials,
    ) -> tuple[str, dict[str, str], Any]:
        """Attach OpenCode Zen bearer token."""
        modified_headers = dict(headers)
        if credentials.access_token:
            modified_headers["Authorization"] = f"Bearer {credentials.access_token}"
        return url, modified_headers, body

    def get_auth_methods(self) -> list[dict[str, str]]:
        """OpenCode Zen supports API keys."""
        return [
            {"type": "api", "label": "Enter OpenCode API key"},
        ]
