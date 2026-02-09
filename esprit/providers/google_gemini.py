"""
Google Gemini OAuth authentication provider.

Supports authentication with Google AI Studio / Gemini API.
"""

import logging
import time
from typing import Any

import httpx

from esprit.providers.base import (
    AuthCallbackResult,
    AuthMethod,
    AuthorizationResult,
    OAuthCredentials,
    ProviderAuth,
)

logger = logging.getLogger(__name__)

# Google OAuth configuration
CLIENT_ID = "764086051850-6qr4p6gpi6hn506pt8ejuq83di341hur.apps.googleusercontent.com"
CLIENT_SECRET = ""  # Public client, no secret needed for installed apps
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
GEMINI_API_URL = "https://generativelanguage.googleapis.com"

# Scopes for Gemini API access
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/generative-language",
    "https://www.googleapis.com/auth/generative-language.tuning",
    "https://www.googleapis.com/auth/generative-language.retriever",
]

# Available Gemini models
GEMINI_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-pro",
    "gemini-1.5-flash",
]


class GoogleGeminiProvider(ProviderAuth):
    """Google Gemini OAuth provider."""

    provider_id = "google"
    display_name = "Google Gemini"

    async def authorize(self, **kwargs) -> AuthorizationResult:
        """Start OAuth authorization flow."""
        from esprit.providers.pkce import generate_pkce

        verifier, challenge = generate_pkce()

        params = {
            "client_id": CLIENT_ID,
            "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",  # Out-of-band for CLI
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "access_type": "offline",
            "prompt": "consent",
        }

        from urllib.parse import urlencode
        auth_url = f"{AUTH_URL}?{urlencode(params)}"

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

        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    TOKEN_URL,
                    data={
                        "client_id": CLIENT_ID,
                        "code": code.strip(),
                        "code_verifier": auth_result.verifier,
                        "grant_type": "authorization_code",
                        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
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
                data={
                    "client_id": CLIENT_ID,
                    "refresh_token": credentials.refresh_token,
                    "grant_type": "refresh_token",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
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

        # Set OAuth bearer token
        modified_headers["Authorization"] = f"Bearer {credentials.access_token}"

        # Set content type
        modified_headers["Content-Type"] = "application/json"

        return url, modified_headers, body

    def get_auth_methods(self) -> list[dict[str, str]]:
        """Get available authentication methods."""
        return [
            {"type": "oauth", "label": "Google Account (OAuth)"},
            {"type": "api", "label": "Enter API Key manually"},
        ]
