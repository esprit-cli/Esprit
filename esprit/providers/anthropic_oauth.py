"""
Anthropic Claude Pro/Max OAuth authentication provider.

Supports authentication with Claude Pro/Max subscriptions via OAuth 2.0 + PKCE.
Based on OpenCode's anthropic-auth plugin.
"""

import logging
import time
from typing import Any
from urllib.parse import urlencode

import httpx

from esprit.providers.base import (
    AuthCallbackResult,
    AuthMethod,
    AuthorizationResult,
    OAuthCredentials,
    ProviderAuth,
)
from esprit.providers.pkce import generate_pkce

logger = logging.getLogger(__name__)

# OpenCode's registered OAuth client ID for Anthropic
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

# API endpoints
AUTH_URL_MAX = "https://claude.ai/oauth/authorize"
AUTH_URL_CONSOLE = "https://console.anthropic.com/oauth/authorize"
TOKEN_URL = "https://console.anthropic.com/v1/oauth/token"
API_URL = "https://api.anthropic.com"
REDIRECT_URI = "https://console.anthropic.com/oauth/code/callback"

# Required beta headers for OAuth
OAUTH_BETA_HEADERS = [
    "oauth-2025-04-20",
    "interleaved-thinking-2025-05-14",
    "fine-grained-tool-streaming-2025-05-14",
]


class AnthropicOAuthProvider(ProviderAuth):
    """Anthropic Claude Pro/Max OAuth provider."""

    provider_id = "anthropic"
    display_name = "Claude Pro/Max"

    def __init__(self, mode: str = "max"):
        """
        Initialize provider.
        
        Args:
            mode: "max" for Claude Pro/Max subscription, "console" for API key creation
        """
        self.mode = mode

    async def authorize(self, **kwargs) -> AuthorizationResult:
        """Start OAuth authorization flow."""
        verifier, challenge = generate_pkce()

        # Choose auth URL based on mode
        base_url = AUTH_URL_MAX if self.mode == "max" else AUTH_URL_CONSOLE

        # Scopes: org:create_api_key user:profile user:inference
        params = {
            "code": "true",
            "client_id": CLIENT_ID,
            "response_type": "code",
            "redirect_uri": REDIRECT_URI,
            "scope": "org:create_api_key user:profile user:inference",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": verifier,
        }

        auth_url = f"{base_url}?{urlencode(params)}"

        return AuthorizationResult(
            url=auth_url,
            instructions="Paste the authorization code here:",
            method=AuthMethod.CODE,
            verifier=verifier,
        )

    async def callback(
        self,
        auth_result: AuthorizationResult,
        code: str | None = None,
    ) -> AuthCallbackResult:
        """Exchange authorization code for tokens."""
        if not code:
            return AuthCallbackResult(
                success=False,
                error="Authorization code is required",
            )

        # Parse code - format is "{auth_code}#{state}" from Anthropic
        parts = code.split("#")
        auth_code = parts[0]
        state = parts[1] if len(parts) > 1 else ""

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    TOKEN_URL,
                    json={
                        "code": auth_code,
                        "state": state,
                        "grant_type": "authorization_code",
                        "client_id": CLIENT_ID,
                        "redirect_uri": REDIRECT_URI,
                        "code_verifier": auth_result.verifier,
                    },
                    headers={"Content-Type": "application/json"},
                    timeout=30,
                )

                if not response.is_success:
                    logger.error(f"Token exchange failed: {response.status_code} {response.text}")
                    return AuthCallbackResult(
                        success=False,
                        error=f"Token exchange failed: {response.status_code}",
                    )

                data = response.json()

                credentials = OAuthCredentials(
                    type="oauth",
                    access_token=data.get("access_token"),
                    refresh_token=data.get("refresh_token"),
                    expires_at=int(time.time() * 1000) + (data.get("expires_in", 3600) * 1000),
                )

                return AuthCallbackResult(
                    success=True,
                    credentials=credentials,
                )

        except httpx.RequestError as e:
            logger.exception("Token exchange request failed")
            return AuthCallbackResult(
                success=False,
                error=f"Network error: {e}",
            )

    async def refresh_token(
        self,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """Refresh an expired access token."""
        if not credentials.refresh_token:
            raise ValueError("No refresh token available")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                TOKEN_URL,
                json={
                    "grant_type": "refresh_token",
                    "refresh_token": credentials.refresh_token,
                    "client_id": CLIENT_ID,
                },
                headers={"Content-Type": "application/json"},
                timeout=30,
            )

            if not response.is_success:
                raise ValueError(f"Token refresh failed: {response.status_code}")

            data = response.json()

            return OAuthCredentials(
                type="oauth",
                access_token=data.get("access_token"),
                refresh_token=data.get("refresh_token", credentials.refresh_token),
                expires_at=int(time.time() * 1000) + (data.get("expires_in", 3600) * 1000),
                account_id=credentials.account_id,
                enterprise_url=credentials.enterprise_url,
                extra=credentials.extra,
            )

    def modify_request(
        self,
        url: str,
        headers: dict[str, str],
        body: Any,
        credentials: OAuthCredentials,
    ) -> tuple[str, dict[str, str], Any]:
        """Modify API request for OAuth authentication."""
        modified_headers = dict(headers)

        # Remove API key header if present (OpenCode does this)
        modified_headers.pop("x-api-key", None)
        modified_headers.pop("X-Api-Key", None)

        # Set OAuth bearer token
        modified_headers["Authorization"] = f"Bearer {credentials.access_token}"

        # Add required beta headers (exact match from OpenCode)
        existing_beta = modified_headers.get("anthropic-beta", "")
        existing_betas = [b.strip() for b in existing_beta.split(",") if b.strip()]
        merged_betas = list(set(OAUTH_BETA_HEADERS + existing_betas))
        modified_headers["anthropic-beta"] = ",".join(merged_betas)

        # Set user agent to match Claude CLI (OpenCode uses this)
        modified_headers["User-Agent"] = "claude-cli/2.1.2 (external, cli)"

        return url, modified_headers, body

    def get_auth_methods(self) -> list[dict[str, str]]:
        """Get available authentication methods."""
        return [
            {"type": "oauth", "label": "Claude Pro/Max (subscription)"},
            {"type": "oauth", "label": "Create API Key (via console)"},
            {"type": "api", "label": "Enter API Key manually"},
        ]


async def create_api_key_via_oauth(credentials: OAuthCredentials) -> str | None:
    """
    Create an API key using OAuth credentials.
    
    This allows users to create an API key from their Claude subscription.
    """
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{API_URL}/api/oauth/claude_cli/create_api_key",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {credentials.access_token}",
            },
            timeout=30,
        )

        if not response.is_success:
            return None

        data = response.json()
        return data.get("raw_key")
