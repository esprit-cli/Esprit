"""
Credential storage and retrieval for Esprit CLI.

Stores credentials in ~/.esprit/credentials.json
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypedDict

import requests


API_BASE_URL = os.getenv("ESPRIT_API_URL", "https://esprit.dev/api/v1").rstrip("/")
_VERIFICATION_CACHE_TTL_SECONDS = 300
_PAID_PLANS = {"pro", "team", "enterprise"}
_verification_cache: dict[str, Any] = {
    "token": None,
    "checked_at": 0.0,
    "result": None,
}


class Credentials(TypedDict, total=False):
    """Stored credential structure."""

    access_token: str
    refresh_token: str
    expires_at: int  # Unix timestamp
    user_id: str
    email: str
    full_name: str | None
    plan: str  # 'free', 'pro', 'team'


class SubscriptionVerification(TypedDict, total=False):
    valid: bool
    plan: str
    quota_remaining: dict[str, int]
    cloud_enabled: bool
    available_models: list[str]
    error: str


def get_credentials_path() -> Path:
    """Get the path to the credentials file."""
    esprit_dir = Path.home() / ".esprit"
    esprit_dir.mkdir(parents=True, exist_ok=True)
    return esprit_dir / "credentials.json"


def get_credentials() -> Credentials | None:
    """Load credentials from disk."""
    creds_path = get_credentials_path()

    if not creds_path.exists():
        return None

    try:
        with creds_path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def save_credentials(credentials: Credentials) -> None:
    """Save credentials to disk."""
    creds_path = get_credentials_path()

    # Ensure parent directory exists
    creds_path.parent.mkdir(parents=True, exist_ok=True)

    # Write credentials with restricted permissions
    with creds_path.open("w", encoding="utf-8") as f:
        json.dump(credentials, f, indent=2)

    # Set file permissions to owner-only (Unix)
    if os.name != "nt":
        os.chmod(creds_path, 0o600)


def clear_credentials() -> None:
    """Remove stored credentials."""
    creds_path = get_credentials_path()

    if creds_path.exists():
        creds_path.unlink()


def is_authenticated() -> bool:
    """Check if user is authenticated with valid credentials."""
    creds = get_credentials()

    if not creds:
        return False

    if "access_token" not in creds:
        return False

    # Check if token has expired
    expires_at = creds.get("expires_at")
    if expires_at:
        now = int(datetime.now(tz=timezone.utc).timestamp())
        if now >= expires_at:
            return False

    return True


def get_auth_token() -> str | None:
    """Get the current access token if authenticated."""
    if not is_authenticated():
        return None

    creds = get_credentials()
    return creds.get("access_token") if creds else None


def get_user_plan() -> str:
    """Get the current user's plan."""
    creds = get_credentials()
    if creds:
        return creds.get("plan", "free")
    return "free"


def get_user_email() -> str | None:
    """Get the current user's email."""
    creds = get_credentials()
    return creds.get("email") if creds else None


def get_user_id() -> str | None:
    """Get the current user's ID."""
    creds = get_credentials()
    return creds.get("user_id") if creds else None


def verify_subscription(
    access_token: str | None = None,
    *,
    force_refresh: bool = False,
) -> SubscriptionVerification:
    """Verify subscription status with the Esprit API."""
    token = access_token or get_auth_token()
    if not token:
        return {
            "valid": False,
            "plan": "free",
            "quota_remaining": {"scans": 0, "tokens": 0},
            "cloud_enabled": False,
            "available_models": [],
            "error": "No authentication token available.",
        }

    now = time.time()
    cached_token = _verification_cache.get("token")
    cached_checked_at = float(_verification_cache.get("checked_at") or 0.0)
    cached_result = _verification_cache.get("result")
    if (
        not force_refresh
        and cached_token == token
        and cached_result is not None
        and now - cached_checked_at < _VERIFICATION_CACHE_TTL_SECONDS
    ):
        return cached_result

    headers = {"Authorization": f"Bearer {token}"}
    default_result: SubscriptionVerification = {
        "valid": False,
        "plan": get_user_plan(),
        "quota_remaining": {"scans": 0, "tokens": 0},
        "cloud_enabled": False,
        "available_models": [],
    }

    try:
        response = requests.get(
            f"{API_BASE_URL}/subscription/verify",
            headers=headers,
            timeout=15,
        )
        if response.status_code != 200:
            result = dict(default_result)
            result["error"] = f"Subscription API returned HTTP {response.status_code}."
        else:
            payload = response.json()
            plan_value = str(payload.get("plan", default_result["plan"]))
            cloud_default = plan_value.lower() in _PAID_PLANS
            result = dict(default_result)
            result.update(
                {
                    "valid": bool(payload.get("valid", False)),
                    "plan": plan_value,
                    "quota_remaining": payload.get("quota_remaining", {"scans": 0, "tokens": 0}),
                    "cloud_enabled": bool(payload.get("cloud_enabled", cloud_default)),
                    "available_models": payload.get("available_models", []),
                }
            )
    except (requests.RequestException, ValueError) as exc:
        result = dict(default_result)
        result["error"] = f"Subscription verification failed: {exc}"

    _verification_cache["token"] = token
    _verification_cache["checked_at"] = now
    _verification_cache["result"] = result
    return result
