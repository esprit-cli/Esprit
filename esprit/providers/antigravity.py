"""
Antigravity OAuth provider for Esprit.

Authenticates with Google OAuth to access Claude and Gemini models
via Google's Cloud Code API (daily-cloudcode-pa.googleapis.com).
Uses the official Gemini CLI OAuth client for authentication.
"""

import asyncio
import hashlib
import html as html_mod
import json
import logging
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
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

# OAuth configuration — uses the Antigravity (Cloud Code IDE) client ID
# (an "installed application" client where embedding the secret in source is expected)
# See: https://developers.google.com/identity/protocols/oauth2#installed
CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
CLIENT_SECRET = "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"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v1/userinfo"

SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

# Cloud Code endpoints (in fallback order: sandbox daily → sandbox autopush → prod)
ENDPOINTS = [
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://autopush-cloudcode-pa.sandbox.googleapis.com",
    "https://cloudcode-pa.googleapis.com",
]

# Endpoints for loadCodeAssist (prod first, then sandbox)
LOAD_ENDPOINTS = [
    "https://cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://autopush-cloudcode-pa.sandbox.googleapis.com",
]

CALLBACK_TIMEOUT = 300  # 5 minutes

# Available models
ANTIGRAVITY_MODELS = {
    "claude-opus-4-6-thinking",
    "claude-opus-4-5-thinking",
    "claude-sonnet-4-5-thinking",
    "claude-sonnet-4-5",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash-thinking",
    "gemini-2.5-pro",
    "gemini-3-flash",
    "gemini-3-pro-high",
    "gemini-3-pro-image",
    "gemini-3-pro-low",
}

# Fallback chain: ordered by capability (high → low).
# When a model fails persistently, try the next one down.
ANTIGRAVITY_FALLBACK_CHAIN: list[str] = [
    "claude-opus-4-6-thinking",
    "claude-opus-4-5-thinking",
    "claude-sonnet-4-5-thinking",
    "claude-sonnet-4-5",
    "gemini-3-pro-high",
    "gemini-3-pro-low",
    "gemini-2.5-pro",
    "gemini-3-flash",
    "gemini-2.5-flash",
    "gemini-2.5-flash-thinking",
    "gemini-2.5-flash-lite",
]


def get_fallback_models(current_model: str) -> list[str]:
    """Get ordered list of fallback models to try after the current one fails."""
    bare = current_model.split("/", 1)[-1] if "/" in current_model else current_model
    try:
        idx = ANTIGRAVITY_FALLBACK_CHAIN.index(bare)
        return ANTIGRAVITY_FALLBACK_CHAIN[idx + 1:]
    except ValueError:
        # Model not in chain — return everything below gemini-3-flash
        try:
            idx = ANTIGRAVITY_FALLBACK_CHAIN.index("gemini-3-flash")
            return ANTIGRAVITY_FALLBACK_CHAIN[idx:]
        except ValueError:
            return []

HTML_SUCCESS = """<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <title>Esprit - Antigravity Connected</title>
    <style>
      body {
        font-family: system-ui, -apple-system, sans-serif;
        display: flex; justify-content: center; align-items: center;
        height: 100vh; margin: 0; background: #0f0f0f; color: #e5e5e5;
      }
      .container { text-align: center; padding: 2rem; }
      h1 { color: #22c55e; margin-bottom: 1rem; }
      p { color: #737373; }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>✓ Antigravity Connected</h1>
      <p>You can close this window and return to Esprit.</p>
    </div>
    <script>setTimeout(() => window.close(), 2000)</script>
  </body>
</html>"""


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code verifier and challenge (S256)."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode()).digest()
    import base64

    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def _generate_state() -> str:
    import base64

    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def _find_free_port() -> int:
    """Find an available port for the OAuth callback server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _CallbackHandler(BaseHTTPRequestHandler):
    server: "_CallbackServer"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/oauth2callback":
            params = parse_qs(parsed.query)
            error = params.get("error", [None])[0]
            if error:
                self.server.error = params.get("error_description", [error])[0]
                self._send(400, f"<h1>Error: {html_mod.escape(str(self.server.error))}</h1>")
                return
            code = params.get("code", [None])[0]
            state = params.get("state", [None])[0]
            if not code or state != self.server.expected_state:
                self.server.error = "Invalid callback"
                self._send(400, "<h1>Invalid callback</h1>")
                return
            self.server.code = code
            self._send(200, HTML_SUCCESS)
        else:
            self.send_error(404)

    def _send(self, status: int, html: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html.encode())


class _CallbackServer(HTTPServer):
    def __init__(self, port: int, expected_state: str):
        super().__init__(("127.0.0.1", port), _CallbackHandler)
        self.expected_state = expected_state
        self.code: str | None = None
        self.error: str | None = None


async def _discover_project(access_token: str) -> tuple[str | None, str | None]:
    """Discover Cloud Code project ID via loadCodeAssist.

    Returns (project_id, managed_project_id).
    """
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "google-api-nodejs-client/9.15.1",
        "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
        "Client-Metadata": json.dumps(
            {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}
        ),
    }
    body = {
        "metadata": {
            "ideType": "IDE_UNSPECIFIED",
            "platform": "PLATFORM_UNSPECIFIED",
            "pluginType": "GEMINI",
        }
    }

    for endpoint in LOAD_ENDPOINTS:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{endpoint}/v1internal:loadCodeAssist",
                    headers=headers,
                    json=body,
                )
                if resp.is_success:
                    data = resp.json()
                    # cloudaicompanionProject can be a string or an object with .id
                    raw_project = data.get("cloudaicompanionProject")
                    if isinstance(raw_project, dict):
                        project = raw_project.get("id")
                    elif isinstance(raw_project, str):
                        project = raw_project
                    else:
                        project = data.get("projectId")
                    managed = data.get("managedProjectId")
                    if project:
                        return project, managed
        except Exception:
            continue
    return None, None


class AntigravityProvider(ProviderAuth):
    """Antigravity provider for free Claude/Gemini access via Google Cloud Code."""

    provider_id = "antigravity"
    display_name = "Antigravity (Free Claude/Gemini)"

    def __init__(self) -> None:
        self._pending_auth: dict[str, Any] = {}

    async def authorize(self, **kwargs: Any) -> AuthorizationResult:
        verifier, challenge = _generate_pkce()
        state = _generate_state()
        port = _find_free_port()
        redirect_uri = f"http://127.0.0.1:{port}/oauth2callback"

        params = {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": " ".join(SCOPES),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
        }
        auth_url = f"{AUTH_URL}?{urlencode(params)}"

        self._pending_auth = {
            "verifier": verifier,
            "state": state,
            "redirect_uri": redirect_uri,
            "port": port,
        }

        return AuthorizationResult(
            url=auth_url,
            instructions="Complete Google login in browser.",
            method=AuthMethod.AUTO,
            verifier=verifier,
        )

    async def callback(
        self, auth_result: AuthorizationResult, code: str | None = None
    ) -> AuthCallbackResult:
        verifier = auth_result.verifier or self._pending_auth.get("verifier")
        state = self._pending_auth.get("state")
        redirect_uri = self._pending_auth.get("redirect_uri")
        port = self._pending_auth.get("port")

        if not verifier or not state or not redirect_uri or not port:
            return AuthCallbackResult(success=False, error="Missing PKCE/state/port")

        # Start callback server
        server = _CallbackServer(port, state)
        server_thread = threading.Thread(target=server.handle_request, daemon=True)
        server_thread.start()
        try:
            await asyncio.to_thread(server_thread.join, CALLBACK_TIMEOUT)
        finally:
            try:
                server.shutdown()
                server.server_close()
            except Exception:
                pass

        if server.error:
            return AuthCallbackResult(success=False, error=server.error)
        if not server.code:
            return AuthCallbackResult(success=False, error="Authorization timed out")

        try:
            async with httpx.AsyncClient() as client:
                # Exchange code for tokens
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "authorization_code",
                        "code": server.code,
                        "redirect_uri": redirect_uri,
                        "client_id": CLIENT_ID,
                        "client_secret": CLIENT_SECRET,
                        "code_verifier": verifier,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    timeout=30,
                )
                if not resp.is_success:
                    return AuthCallbackResult(
                        success=False,
                        error=f"Token exchange failed: {resp.status_code}",
                    )

                tokens = resp.json()
                access_token = tokens.get("access_token")
                refresh_token = tokens.get("refresh_token")
                expires_at = int(time.time() * 1000) + (
                    tokens.get("expires_in", 3600) * 1000
                )

                # Get user email
                email = "unknown"
                try:
                    info = await client.get(
                        USERINFO_URL,
                        headers={"Authorization": f"Bearer {access_token}"},
                        timeout=10,
                    )
                    if info.is_success:
                        email = info.json().get("email", "unknown")
                except Exception:
                    pass

                # Discover project ID
                project_id, managed_project_id = await _discover_project(access_token)

                credentials = OAuthCredentials(
                    type="oauth",
                    access_token=access_token,
                    refresh_token=refresh_token,
                    expires_at=expires_at,
                    extra={
                        "email": email,
                        "project_id": project_id,
                        "managed_project_id": managed_project_id,
                    },
                )

                return AuthCallbackResult(success=True, credentials=credentials)

        except Exception as e:
            return AuthCallbackResult(
                success=False, error=f"Token exchange error: {e}"
            )

    async def refresh_token(self, credentials: OAuthCredentials) -> OAuthCredentials:
        if not credentials.refresh_token:
            raise ValueError("No refresh token available")

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": credentials.refresh_token,
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            if not resp.is_success:
                raise ValueError(f"Token refresh failed: {resp.status_code}")

            tokens = resp.json()
            return OAuthCredentials(
                type="oauth",
                access_token=tokens.get("access_token"),
                refresh_token=tokens.get("refresh_token", credentials.refresh_token),
                expires_at=int(time.time() * 1000)
                + (tokens.get("expires_in", 3600) * 1000),
                extra=credentials.extra,
            )

    def modify_request(
        self,
        url: str,
        headers: dict[str, str],
        body: Any,
        credentials: OAuthCredentials,
    ) -> tuple[str, dict[str, str], Any]:
        import platform as plat

        modified = dict(headers)
        modified["Authorization"] = f"Bearer {credentials.access_token}"
        modified["Content-Type"] = "application/json"
        system = plat.system().lower()
        arch = plat.machine()
        modified["User-Agent"] = f"antigravity/1.15.8 {system}/{arch}"
        modified["X-Goog-Api-Client"] = (
            "google-cloud-sdk vscode_cloudshelleditor/0.1"
        )
        modified["Client-Metadata"] = json.dumps(
            {"ideType": "IDE_UNSPECIFIED", "platform": "PLATFORM_UNSPECIFIED", "pluginType": "GEMINI"}
        )
        return url, modified, body

    def get_auth_methods(self) -> list[dict[str, str]]:
        return [
            {"type": "oauth", "label": "Login with Google (Antigravity)"},
        ]
