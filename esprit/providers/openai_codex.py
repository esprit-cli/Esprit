"""
OpenAI ChatGPT Plus/Pro (Codex) OAuth authentication provider.

Supports authentication with ChatGPT Plus/Pro subscriptions via OAuth 2.0.
Implements both device flow (headless) and browser OAuth flow (localhost callback).
"""

import asyncio
import base64
import hashlib
import json
import logging
import platform
import secrets
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from esprit.providers.base import (
    AuthCallbackResult,
    AuthMethod,
    AuthorizationResult,
    OAuthCredentials,
    ProviderAuth,
)

logger = logging.getLogger(__name__)

# OpenAI OAuth configuration
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"

# API endpoints
DEVICE_CODE_URL = f"{ISSUER}/api/accounts/deviceauth/usercode"
DEVICE_TOKEN_URL = f"{ISSUER}/api/accounts/deviceauth/token"
TOKEN_URL = f"{ISSUER}/oauth/token"
CODEX_API_URL = "https://chatgpt.com/backend-api/codex/responses"

# Browser OAuth settings
OAUTH_PORT = 1455
OAUTH_TIMEOUT = 300  # 5 minutes

# Allowed models for Codex OAuth
ALLOWED_CODEX_MODELS = {
    "gpt-5.3-codex",
    "gpt-5.2-codex",
    "gpt-5.2",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5.1",
    "gpt-5-codex",
    "gpt-5",
    "gpt-5-codex-mini",
}

# Polling safety margin
POLLING_SAFETY_MARGIN_MS = 3000

# Dummy API key for OAuth (removed from requests)
OAUTH_DUMMY_KEY = "oauth-dummy-key"


# HTML responses for OAuth callback
HTML_SUCCESS = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Esprit - Authorization Successful</title>
    <style>
      body {
        font-family: system-ui, -apple-system, sans-serif;
        display: flex;
        justify-content: center;
        align-items: center;
        height: 100vh;
        margin: 0;
        background: #0f0f0f;
        color: #e5e5e5;
      }
      .container { text-align: center; padding: 2rem; }
      h1 { color: #22c55e; margin-bottom: 1rem; }
      p { color: #737373; }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>✓ Authorization Successful</h1>
      <p>You can close this window and return to Esprit.</p>
    </div>
    <script>setTimeout(() => window.close(), 2000)</script>
  </body>
</html>"""


HTML_ERROR = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Esprit - Authorization Failed</title>
    <style>
      body {
        font-family: system-ui, -apple-system, sans-serif;
        display: flex;
        justify-content: center;
        align-items: center;
        height: 100vh;
        margin: 0;
        background: #0f0f0f;
        color: #e5e5e5;
      }
      .container { text-align: center; padding: 2rem; }
      h1 { color: #ef4444; margin-bottom: 1rem; }
      p { color: #737373; }
      .error { color: #fca5a5; font-family: monospace; margin-top: 1rem; padding: 1rem; background: #2d1515; border-radius: 0.5rem; }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>✗ Authorization Failed</h1>
      <p>An error occurred during authorization.</p>
      <div class="error">{error}</div>
    </div>
  </body>
</html>"""


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge."""
    chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(secrets.choice(chars) for _ in range(43))
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _generate_state() -> str:
    """Generate random state for CSRF protection."""
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


class OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for OAuth callback."""

    server: "OAuthCallbackServer"

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress HTTP server logs."""
        pass

    def do_GET(self) -> None:
        """Handle OAuth callback GET request."""
        parsed = urlparse(self.path)

        if parsed.path == "/auth/callback":
            params = parse_qs(parsed.query)

            error = params.get("error", [None])[0]
            if error:
                error_desc = params.get("error_description", [error])[0]
                self.server.error = error_desc
                self._send_html(HTML_ERROR.format(error=error_desc))
                return

            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]

            if not code:
                self.server.error = "Missing authorization code"
                self._send_html(HTML_ERROR.format(error="Missing authorization code"), 400)
                return

            if state != self.server.expected_state:
                self.server.error = "Invalid state - potential CSRF attack"
                self._send_html(HTML_ERROR.format(error="Invalid state"), 400)
                return

            self.server.code = code
            self._send_html(HTML_SUCCESS)
        else:
            self.send_error(404)

    def _send_html(self, html: str, status: int = 200) -> None:
        """Send HTML response."""
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())


class OAuthCallbackServer(HTTPServer):
    """HTTP server for OAuth callback with result storage."""

    def __init__(self, port: int, expected_state: str):
        super().__init__(("localhost", port), OAuthCallbackHandler)
        self.expected_state = expected_state
        self.code: str | None = None
        self.error: str | None = None


def _decode_jwt_payload(token: str) -> dict[str, Any] | None:
    """Decode JWT payload without verification."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        # Add padding if needed
        payload = parts[1]
        padding = 4 - len(payload) % 4
        if padding != 4:
            payload += "=" * padding
        decoded = base64.urlsafe_b64decode(payload)
        return json.loads(decoded)
    except Exception:
        return None


def _extract_account_id(tokens: dict[str, Any]) -> str | None:
    """Extract ChatGPT account ID from JWT tokens."""
    for token_key in ["id_token", "access_token"]:
        token = tokens.get(token_key)
        if not token:
            continue
        claims = _decode_jwt_payload(token)
        if not claims:
            continue
        # Try different claim locations
        account_id = (
            claims.get("chatgpt_account_id")
            or claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
        )
        if account_id:
            return account_id
        # Try organizations
        orgs = claims.get("organizations", [])
        if orgs and isinstance(orgs, list) and orgs[0].get("id"):
            return orgs[0]["id"]
    return None


class OpenAICodexProvider(ProviderAuth):
    """OpenAI ChatGPT Plus/Pro (Codex) OAuth provider.

    Supports two OAuth flows:
    1. Browser OAuth (default) - Opens browser, uses localhost callback
    2. Device flow (headless) - For terminals without browser access
    """

    provider_id = "openai"
    display_name = "ChatGPT Plus/Pro"

    def __init__(self, mode: str = "browser"):
        """
        Initialize provider.

        Args:
            mode: "browser" for localhost callback (default), "headless" for device flow
        """
        self.mode = mode
        self._pending_auth: dict[str, Any] = {}

    async def authorize(self, **kwargs) -> AuthorizationResult:
        """Start OAuth authorization flow."""
        mode = kwargs.get("mode", self.mode)

        if mode == "browser":
            return await self._authorize_browser(**kwargs)
        else:
            return await self._authorize_device(**kwargs)

    async def _authorize_browser(self, **kwargs) -> AuthorizationResult:
        """Start browser-based OAuth flow with localhost callback."""
        verifier, challenge = _generate_pkce()
        state = _generate_state()
        redirect_uri = f"http://localhost:{OAUTH_PORT}/auth/callback"

        # Build authorization URL
        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email offline_access",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": state,
            "originator": "esprit",
        }
        auth_url = f"{ISSUER}/oauth/authorize?{urlencode(params)}"

        # Store pending auth info
        self._pending_auth = {
            "mode": "browser",
            "verifier": verifier,
            "state": state,
            "redirect_uri": redirect_uri,
        }

        return AuthorizationResult(
            url=auth_url,
            instructions="Complete login in browser. A new tab will open automatically.",
            method=AuthMethod.AUTO,  # We'll handle callback automatically
            verifier=verifier,
        )

    async def _authorize_device(self, **kwargs) -> AuthorizationResult:
        """Start device code OAuth flow (headless)."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                DEVICE_CODE_URL,
                json={"client_id": CLIENT_ID},
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "esprit/1.0",
                },
                timeout=30,
            )

            if not response.is_success:
                raise ValueError(f"Failed to initiate device authorization: {response.status_code}")

            data = response.json()

            device_auth_id = data.get("device_auth_id")
            user_code = data.get("user_code")
            interval = max(int(data.get("interval", 5)), 1)

            # Store pending auth info
            self._pending_auth = {
                "mode": "device",
                "device_auth_id": device_auth_id,
                "user_code": user_code,
                "interval": interval,
            }

            return AuthorizationResult(
                url=f"{ISSUER}/codex/device",
                instructions=f"Enter code: {user_code}",
                method=AuthMethod.AUTO,
                device_code=device_auth_id,
                interval=interval,
            )

    async def callback(
        self,
        auth_result: AuthorizationResult,
        code: str | None = None,
    ) -> AuthCallbackResult:
        """Complete the authorization flow."""
        mode = self._pending_auth.get("mode", "browser")

        if mode == "browser":
            return await self._callback_browser(auth_result, code)
        else:
            return await self._callback_device(auth_result, code)

    async def _callback_browser(
        self,
        auth_result: AuthorizationResult,
        code: str | None = None,
    ) -> AuthCallbackResult:
        """Handle browser OAuth callback."""
        verifier = auth_result.verifier or self._pending_auth.get("verifier")
        state = self._pending_auth.get("state")
        redirect_uri = self._pending_auth.get("redirect_uri", f"http://localhost:{OAUTH_PORT}/auth/callback")

        if not verifier or not state:
            return AuthCallbackResult(
                success=False,
                error="Missing PKCE verifier or state",
            )

        # Start callback server and wait for authorization
        server = OAuthCallbackServer(OAUTH_PORT, state)
        server_thread = threading.Thread(target=server.handle_request)
        server_thread.start()

        # Browser is already opened by the caller (commands.py)

        # Wait for callback (with timeout)
        server_thread.join(timeout=OAUTH_TIMEOUT)

        if server.error:
            return AuthCallbackResult(
                success=False,
                error=server.error,
            )

        if not server.code:
            return AuthCallbackResult(
                success=False,
                error="Authorization timed out",
            )

        # Exchange code for tokens
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": server.code,
                        "redirect_uri": redirect_uri,
                        "client_id": CLIENT_ID,
                        "code_verifier": verifier,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30,
                )

                if not response.is_success:
                    return AuthCallbackResult(
                        success=False,
                        error=f"Token exchange failed: {response.status_code}",
                    )

                tokens = response.json()
                account_id = _extract_account_id(tokens)

                credentials = OAuthCredentials(
                    type="oauth",
                    access_token=tokens.get("access_token"),
                    refresh_token=tokens.get("refresh_token"),
                    expires_at=int(time.time() * 1000) + (tokens.get("expires_in", 3600) * 1000),
                    account_id=account_id,
                )

                return AuthCallbackResult(
                    success=True,
                    credentials=credentials,
                )
        except Exception as e:
            return AuthCallbackResult(
                success=False,
                error=f"Token exchange error: {e}",
            )

    async def _callback_device(
        self,
        auth_result: AuthorizationResult,
        code: str | None = None,
    ) -> AuthCallbackResult:
        """Poll for device flow completion."""
        device_auth_id = auth_result.device_code or self._pending_auth.get("device_auth_id")
        user_code = self._pending_auth.get("user_code")
        interval = auth_result.interval or self._pending_auth.get("interval", 5)

        if not device_auth_id or not user_code:
            return AuthCallbackResult(
                success=False,
                error="Missing device authorization info",
            )

        async with httpx.AsyncClient() as client:
            while True:
                try:
                    response = await client.post(
                        DEVICE_TOKEN_URL,
                        json={
                            "device_auth_id": device_auth_id,
                            "user_code": user_code,
                        },
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "esprit/1.0",
                        },
                        timeout=30,
                    )

                    if response.is_success:
                        data = response.json()
                        auth_code = data.get("authorization_code")
                        code_verifier = data.get("code_verifier")

                        # Exchange for tokens
                        token_response = await client.post(
                            TOKEN_URL,
                            data={
                                "grant_type": "authorization_code",
                                "code": auth_code,
                                "redirect_uri": f"{ISSUER}/deviceauth/callback",
                                "client_id": CLIENT_ID,
                                "code_verifier": code_verifier,
                            },
                            headers={"Content-Type": "application/x-www-form-urlencoded"},
                            timeout=30,
                        )

                        if not token_response.is_success:
                            return AuthCallbackResult(
                                success=False,
                                error=f"Token exchange failed: {token_response.status_code}",
                            )

                        tokens = token_response.json()
                        account_id = _extract_account_id(tokens)

                        credentials = OAuthCredentials(
                            type="oauth",
                            access_token=tokens.get("access_token"),
                            refresh_token=tokens.get("refresh_token"),
                            expires_at=int(time.time() * 1000) + (tokens.get("expires_in", 3600) * 1000),
                            account_id=account_id,
                        )

                        return AuthCallbackResult(
                            success=True,
                            credentials=credentials,
                        )

                    # Handle pending/slow_down errors
                    if response.status_code in (403, 404):
                        # Authorization still pending
                        await asyncio.sleep(interval + POLLING_SAFETY_MARGIN_MS / 1000)
                        continue

                    return AuthCallbackResult(
                        success=False,
                        error=f"Authorization failed: {response.status_code}",
                    )

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
        """Refresh an expired access token."""
        if not credentials.refresh_token:
            raise ValueError("No refresh token available")

        async with httpx.AsyncClient() as client:
            response = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": credentials.refresh_token,
                    "client_id": CLIENT_ID,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )

            if not response.is_success:
                raise ValueError(f"Token refresh failed: {response.status_code}")

            tokens = response.json()
            account_id = _extract_account_id(tokens) or credentials.account_id

            return OAuthCredentials(
                type="oauth",
                access_token=tokens.get("access_token"),
                refresh_token=tokens.get("refresh_token", credentials.refresh_token),
                expires_at=int(time.time() * 1000) + (tokens.get("expires_in", 3600) * 1000),
                account_id=account_id,
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

        # Remove any existing authorization headers
        for key in list(modified_headers.keys()):
            if key.lower() == "authorization":
                del modified_headers[key]

        # Set OAuth bearer token
        modified_headers["Authorization"] = f"Bearer {credentials.access_token}"

        # Set account ID header for organization subscriptions
        if credentials.account_id:
            modified_headers["ChatGPT-Account-Id"] = credentials.account_id

        # Set originator and user agent
        modified_headers["originator"] = "esprit"
        system = platform.system().lower()
        release = platform.release()
        arch = platform.machine()
        modified_headers["User-Agent"] = f"esprit/1.0 ({system} {release}; {arch})"

        # Use the standard OpenAI API endpoint (no URL rewriting needed)
        return url, modified_headers, body

    def get_auth_methods(self) -> list[dict[str, str]]:
        """Get available authentication methods."""
        return [
            {"type": "oauth", "label": "ChatGPT Plus/Pro (headless device flow)"},
            {"type": "oauth", "label": "ChatGPT Plus/Pro (browser callback)"},
            {"type": "api", "label": "Enter API Key manually"},
        ]
