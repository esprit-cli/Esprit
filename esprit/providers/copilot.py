"""
GitHub Copilot OAuth authentication provider.

Supports authentication with GitHub Copilot subscriptions via OAuth Device Flow.
"""

import asyncio
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

# GitHub OAuth client ID for Copilot
CLIENT_ID = "Ov23li8tweQw6odWQebz"

# Polling safety margin
POLLING_SAFETY_MARGIN_MS = 3000

# API endpoints
COPILOT_API_URL = "https://api.githubcopilot.com"


def _normalize_domain(url: str) -> str:
    """Normalize a GitHub Enterprise URL to just the domain."""
    return url.replace("https://", "").replace("http://", "").rstrip("/")


def _get_urls(domain: str) -> dict[str, str]:
    """Get OAuth URLs for a GitHub domain."""
    return {
        "device_code": f"https://{domain}/login/device/code",
        "access_token": f"https://{domain}/login/oauth/access_token",
    }


class CopilotProvider(ProviderAuth):
    """GitHub Copilot OAuth provider."""

    provider_id = "github-copilot"
    display_name = "GitHub Copilot"

    def __init__(self, enterprise_url: str | None = None):
        """
        Initialize provider.
        
        Args:
            enterprise_url: GitHub Enterprise URL (optional)
        """
        self.enterprise_url = enterprise_url
        self._pending_auth: dict[str, Any] = {}

    def _get_domain(self) -> str:
        """Get the GitHub domain to use."""
        if self.enterprise_url:
            return _normalize_domain(self.enterprise_url)
        return "github.com"

    def _get_api_url(self) -> str:
        """Get the Copilot API URL."""
        if self.enterprise_url:
            domain = _normalize_domain(self.enterprise_url)
            return f"https://copilot-api.{domain}"
        return COPILOT_API_URL

    async def authorize(self, **kwargs) -> AuthorizationResult:
        """Start OAuth device flow authorization."""
        # Check for enterprise URL in kwargs
        enterprise_url = kwargs.get("enterprise_url")
        if enterprise_url:
            self.enterprise_url = enterprise_url

        domain = self._get_domain()
        urls = _get_urls(domain)

        async with httpx.AsyncClient() as client:
            response = await client.post(
                urls["device_code"],
                json={
                    "client_id": CLIENT_ID,
                    "scope": "read:user",
                },
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "esprit/1.0",
                },
                timeout=30,
            )

            if not response.is_success:
                raise ValueError(f"Failed to initiate device authorization: {response.status_code}")

            data = response.json()

            device_code = data.get("device_code")
            user_code = data.get("user_code")
            verification_uri = data.get("verification_uri")
            interval = data.get("interval", 5)

            # Store pending auth info
            self._pending_auth = {
                "device_code": device_code,
                "user_code": user_code,
                "interval": interval,
                "domain": domain,
            }

            return AuthorizationResult(
                url=verification_uri,
                instructions=f"Enter code: {user_code}",
                method=AuthMethod.AUTO,
                device_code=device_code,
                interval=interval,
            )

    async def callback(
        self,
        auth_result: AuthorizationResult,
        code: str | None = None,
    ) -> AuthCallbackResult:
        """Poll for device flow completion."""
        device_code = auth_result.device_code or self._pending_auth.get("device_code")
        interval = auth_result.interval or self._pending_auth.get("interval", 5)
        domain = self._pending_auth.get("domain", "github.com")
        urls = _get_urls(domain)

        if not device_code:
            return AuthCallbackResult(
                success=False,
                error="Missing device code",
            )

        async with httpx.AsyncClient() as client:
            while True:
                try:
                    response = await client.post(
                        urls["access_token"],
                        json={
                            "client_id": CLIENT_ID,
                            "device_code": device_code,
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                        },
                        headers={
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                            "User-Agent": "esprit/1.0",
                        },
                        timeout=30,
                    )

                    if not response.is_success:
                        return AuthCallbackResult(
                            success=False,
                            error=f"Authorization failed: {response.status_code}",
                        )

                    data = response.json()

                    if "access_token" in data:
                        credentials = OAuthCredentials(
                            type="oauth",
                            access_token=data["access_token"],
                            refresh_token=data["access_token"],  # GitHub uses same token
                            expires_at=0,  # GitHub tokens don't expire
                            enterprise_url=self.enterprise_url,
                        )

                        return AuthCallbackResult(
                            success=True,
                            credentials=credentials,
                        )

                    error = data.get("error")

                    if error == "authorization_pending":
                        await asyncio.sleep(interval + POLLING_SAFETY_MARGIN_MS / 1000)
                        continue

                    if error == "slow_down":
                        # Add 5 seconds per RFC 8628
                        new_interval = data.get("interval", interval + 5)
                        await asyncio.sleep(new_interval + POLLING_SAFETY_MARGIN_MS / 1000)
                        continue

                    if error:
                        return AuthCallbackResult(
                            success=False,
                            error=f"Authorization error: {error}",
                        )

                    # Unknown response, keep polling
                    await asyncio.sleep(interval + POLLING_SAFETY_MARGIN_MS / 1000)

                except httpx.RequestError as e:
                    logger.exception("Device token polling failed")
                    return AuthCallbackResult(
                        success=False,
                        error=f"Network error: {e}",
                    )

    async def refresh_token(
        self,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """
        GitHub OAuth tokens don't expire, so just return the same credentials.
        """
        return credentials

    def modify_request(
        self,
        url: str,
        headers: dict[str, str],
        body: Any,
        credentials: OAuthCredentials,
    ) -> tuple[str, dict[str, str], Any]:
        """Modify API request for OAuth authentication."""
        modified_headers = dict(headers)

        # Remove API key headers if present
        modified_headers.pop("x-api-key", None)
        modified_headers.pop("X-Api-Key", None)
        for key in list(modified_headers.keys()):
            if key.lower() == "authorization":
                del modified_headers[key]

        # Set OAuth bearer token (use refresh_token which is the GitHub OAuth token)
        modified_headers["Authorization"] = f"Bearer {credentials.refresh_token}"

        # Set Copilot-specific headers
        modified_headers["User-Agent"] = "esprit/1.0"
        modified_headers["Openai-Intent"] = "conversation-edits"
        modified_headers["x-initiator"] = "user"

        # Modify URL to use Copilot API
        modified_url = url
        if credentials.enterprise_url:
            domain = _normalize_domain(credentials.enterprise_url)
            modified_url = url.replace(COPILOT_API_URL, f"https://copilot-api.{domain}")

        return modified_url, modified_headers, body

    def get_auth_methods(self) -> list[dict[str, str]]:
        """Get available authentication methods."""
        return [
            {"type": "oauth", "label": "Login with GitHub Copilot"},
            {"type": "oauth", "label": "Login with GitHub Enterprise Copilot"},
        ]


class CopilotEnterpriseProvider(CopilotProvider):
    """GitHub Copilot Enterprise OAuth provider."""

    provider_id = "github-copilot-enterprise"
    display_name = "GitHub Copilot Enterprise"

    def __init__(self, enterprise_url: str):
        """
        Initialize provider.
        
        Args:
            enterprise_url: GitHub Enterprise URL (required)
        """
        if not enterprise_url:
            raise ValueError("Enterprise URL is required for Copilot Enterprise")
        super().__init__(enterprise_url=enterprise_url)
