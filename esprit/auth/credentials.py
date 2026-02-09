"""
Credential storage and retrieval for Esprit CLI.

Stores credentials in ~/.esprit/credentials.json
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict


class Credentials(TypedDict, total=False):
    """Stored credential structure."""

    access_token: str
    refresh_token: str
    expires_at: int  # Unix timestamp
    user_id: str
    email: str
    full_name: str | None
    plan: str  # 'free', 'pro', 'team'


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
