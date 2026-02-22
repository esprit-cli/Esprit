"""
Esprit Subscription provider — Use Your Esprit Subscription.

Routes LLM requests through the Esprit API gateway which proxies to
AWS Bedrock models.  Authentication is handled by the Esprit platform
(Supabase device flow), so users must first run ``esprit provider login esprit``.

The provider reads the platform credentials from ~/.esprit/credentials.json
and injects the Esprit access token into each proxied request.
"""

import logging
import os
import time
from typing import Any

from esprit.providers.base import (
    AuthCallbackResult,
    AuthMethod,
    AuthorizationResult,
    OAuthCredentials,
    ProviderAuth,
)

logger = logging.getLogger(__name__)

# Esprit API base URL for the LLM proxy
API_BASE_URL = os.getenv("ESPRIT_API_URL", "https://esprit.dev/api/v1")

# LLM proxy endpoint — the Esprit backend forwards to AWS Bedrock
LLM_PROXY_URL = f"{API_BASE_URL}/llm/generate"

# User-facing aliases -> Bedrock model IDs exposed by Esprit.
# Keep aliases stable even if backend model IDs change.
ESPRIT_BEDROCK_MODELS: dict[str, str] = {
    "default": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "kimi-k2.5": "moonshotai.kimi-k2.5",
    "kimi-k2": "moonshotai.kimi-k2.5",
    "haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "haiku-4.5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-haiku-4-5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "claude-haiku-4-5-20251001": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
}

# Default model for new users
ESPRIT_DEFAULT_MODEL = "default"


def _load_esprit_credentials() -> OAuthCredentials | None:
    """Load Esprit platform credentials from ~/.esprit/credentials.json.

    Returns an ``OAuthCredentials`` wrapper around the platform token so that
    the rest of the provider machinery (token store, litellm integration) can
    work with it using the same interface as other OAuth providers.
    """
    try:
        from esprit.auth.credentials import get_credentials, is_authenticated

        if not is_authenticated():
            return None

        creds = get_credentials()
        if not creds or not creds.get("access_token"):
            return None

        return OAuthCredentials(
            type="oauth",
            access_token=creds["access_token"],
            refresh_token=creds.get("refresh_token", ""),
            expires_at=int(creds.get("expires_at", 0)) * 1000,  # convert to ms
            account_id=creds.get("user_id"),
            extra={
                "email": creds.get("email", ""),
                "plan": creds.get("plan", "free"),
                "full_name": creds.get("full_name", ""),
            },
        )
    except Exception:
        logger.debug("Failed to load Esprit platform credentials", exc_info=True)
        return None


def resolve_bedrock_model(model_alias: str) -> str:
    """Resolve a subscription model alias to a Bedrock model ID."""
    normalized = model_alias.strip().lower()
    if "anthropic." in normalized or normalized.count(".") >= 2:
        return model_alias
    return ESPRIT_BEDROCK_MODELS.get(normalized, model_alias)


class EspritSubsProvider(ProviderAuth):
    """Provider that uses the user's Esprit subscription to access AWS Bedrock models.

    Authentication is handled by the Esprit platform login (``esprit login``).
    The provider does **not** perform its own OAuth dance — it piggybacks on the
    platform credentials stored in ``~/.esprit/credentials.json``.
    """

    provider_id = "esprit"
    display_name = "Esprit (Use Your Subscription)"

    # ── Authorization flow ──────────────────────────────────────────

    async def authorize(self, **kwargs: Any) -> AuthorizationResult:
        """Start the Esprit platform device-flow login.

        This delegates to the same device-flow used by ``esprit login``.
        The URL points to ``esprit.dev/device`` where the user enters the code.
        """
        from esprit.auth.client import SupabaseAuthClient, API_BASE_URL as AUTH_API_URL

        client = SupabaseAuthClient()

        # Request device code from the Esprit API
        import requests as sync_requests

        try:
            response = sync_requests.post(
                f"{AUTH_API_URL}/auth/device/code",
                json={"client_id": "esprit-cli"},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            raise RuntimeError(f"Failed to start Esprit device login: {exc}") from exc

        return AuthorizationResult(
            url=data["verification_uri_complete"],
            instructions=(
                f"Enter code [bold yellow]{data['user_code']}[/] at "
                f"[cyan]{data['verification_uri']}[/]"
            ),
            method=AuthMethod.AUTO,
            device_code=data["device_code"],
            interval=data.get("interval", 5),
        )

    async def callback(
        self,
        auth_result: AuthorizationResult,
        code: str | None = None,
    ) -> AuthCallbackResult:
        """Poll until the user completes the device-flow authorization."""
        import requests as sync_requests
        from esprit.auth.client import API_BASE_URL as AUTH_API_URL
        from esprit.auth.credentials import save_credentials

        device_code = auth_result.device_code
        if not device_code:
            return AuthCallbackResult(success=False, error="Missing device code")

        max_wait = 300  # 5 minutes
        start = time.time()

        while time.time() - start < max_wait:
            await _async_sleep(auth_result.interval)

            try:
                resp = sync_requests.post(
                    f"{AUTH_API_URL}/auth/device/token",
                    json={
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    timeout=30,
                )

                if resp.status_code == 200:
                    token_data = resp.json()

                    # Persist platform credentials
                    platform_creds = {
                        "access_token": token_data["access_token"],
                        "refresh_token": "",
                        "expires_at": token_data.get("expires_in", 0) + int(time.time()),
                        "user_id": token_data.get("user_id", ""),
                        "email": token_data.get("email", ""),
                        "full_name": token_data.get("full_name", ""),
                        "plan": token_data.get("plan", "free"),
                    }
                    save_credentials(platform_creds)

                    # Return provider-level credentials
                    oauth_creds = OAuthCredentials(
                        type="oauth",
                        access_token=token_data["access_token"],
                        refresh_token="",
                        expires_at=(
                            token_data.get("expires_in", 0) + int(time.time())
                        ) * 1000,
                        account_id=token_data.get("user_id", ""),
                        extra={
                            "email": token_data.get("email", ""),
                            "plan": token_data.get("plan", "free"),
                            "full_name": token_data.get("full_name", ""),
                        },
                    )

                    return AuthCallbackResult(success=True, credentials=oauth_creds)

                if resp.status_code == 400:
                    error_data = resp.json()
                    error = error_data.get("detail", {})
                    error_code = (
                        error.get("error", "") if isinstance(error, dict) else str(error)
                    )
                    if "authorization_pending" in error_code:
                        continue
                    if "expired_token" in error_code:
                        return AuthCallbackResult(
                            success=False, error="Device code expired. Try again."
                        )
                    return AuthCallbackResult(
                        success=False, error=f"Auth failed: {error_code}"
                    )

            except Exception:  # noqa: BLE001
                # Network blip — keep polling
                continue

        return AuthCallbackResult(
            success=False, error="Authorization timed out. Please try again."
        )

    # ── Token refresh ───────────────────────────────────────────────

    async def refresh_token(
        self,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """Refresh Esprit platform credentials.

        The Esprit platform currently issues long-lived tokens; if a refresh
        token is available we exchange it, otherwise we return the existing
        credentials and let the user re-login when they truly expire.
        """
        # Reload from disk — the platform may have been refreshed elsewhere
        reloaded = _load_esprit_credentials()
        if reloaded and reloaded.access_token:
            return reloaded

        # If the platform has a refresh mechanism in the future, call it here.
        logger.warning(
            "Esprit token appears expired. Run 'esprit provider login esprit' to re-authenticate."
        )
        return credentials

    # ── Request modification ────────────────────────────────────────

    def modify_request(
        self,
        url: str,
        headers: dict[str, str],
        body: Any,
        credentials: OAuthCredentials,
    ) -> tuple[str, dict[str, str], Any]:
        """Rewrite the request so it targets the Esprit LLM proxy.

        The proxy endpoint accepts an OpenAI-compatible chat/completions body
        and forwards it to the appropriate AWS Bedrock model.

        Headers:
          - Authorization: Bearer <esprit-platform-token>
          - X-Esprit-Provider: bedrock
          - X-Esprit-Model: <full bedrock model id>
        """
        modified_headers = dict(headers)

        # Set Esprit authorization
        modified_headers["Authorization"] = f"Bearer {credentials.access_token}"
        modified_headers["X-Esprit-Provider"] = "bedrock"
        modified_headers["Content-Type"] = "application/json"

        # Extract and resolve the model
        if isinstance(body, dict):
            raw_model = body.get("model", ESPRIT_DEFAULT_MODEL)
            # Strip provider prefix (e.g. "esprit/claude-haiku-4-5" -> "claude-haiku-4-5")
            if isinstance(raw_model, str) and "/" in raw_model:
                raw_model = raw_model.split("/", 1)[1]
            bedrock_model = resolve_bedrock_model(raw_model)
            modified_headers["X-Esprit-Model"] = bedrock_model
            body = dict(body)
            body["model"] = bedrock_model

        # Route to Esprit's proxy
        modified_url = LLM_PROXY_URL

        return modified_url, modified_headers, body

    # ── Helpers ─────────────────────────────────────────────────────

    def get_auth_methods(self) -> list[dict[str, str]]:
        return [
            {"type": "oauth", "label": "Login with Esprit (subscription)"},
        ]


async def _async_sleep(seconds: int) -> None:
    """Async-compatible sleep."""
    import asyncio

    await asyncio.sleep(seconds)
