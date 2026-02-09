"""
Secure token storage for provider OAuth credentials.

Uses the same credential format as OpenCode for compatibility,
but stores in Esprit's own location (~/.esprit/providers.json).
"""

import json
import os
from pathlib import Path
from typing import Any

from esprit.providers.base import OAuthCredentials


def _opencode_format_to_esprit(data: dict[str, Any]) -> OAuthCredentials:
    """Convert OpenCode-style credential format to Esprit OAuthCredentials."""
    cred_type = data.get("type", "oauth")

    if cred_type == "oauth":
        return OAuthCredentials(
            type="oauth",
            access_token=data.get("access"),
            refresh_token=data.get("refresh"),
            expires_at=data.get("expires"),
            account_id=data.get("accountId"),
            enterprise_url=data.get("enterpriseUrl"),
        )
    elif cred_type == "api":
        return OAuthCredentials(
            type="api",
            access_token=data.get("key"),
        )
    elif cred_type == "wellknown":
        env_var = data.get("key", "")
        token = data.get("token") or os.environ.get(env_var, "")
        return OAuthCredentials(
            type="api",
            access_token=token,
        )

    return OAuthCredentials(type=cred_type)


def _esprit_to_opencode_format(creds: OAuthCredentials) -> dict[str, Any]:
    """Convert Esprit OAuthCredentials to OpenCode-style format."""
    if creds.type == "oauth":
        result: dict[str, Any] = {
            "type": "oauth",
            "access": creds.access_token,
            "refresh": creds.refresh_token,
            "expires": creds.expires_at,
        }
        if creds.account_id:
            result["accountId"] = creds.account_id
        if creds.enterprise_url:
            result["enterpriseUrl"] = creds.enterprise_url
        return result
    elif creds.type == "api":
        return {
            "type": "api",
            "key": creds.access_token,
        }

    return {"type": creds.type}


class TokenStore:
    """
    Secure storage for provider OAuth tokens.

    Uses OpenCode-compatible format for easy import/export,
    stored in ~/.esprit/providers.json
    """

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or Path.home() / ".esprit"
        self.providers_file = self.config_dir / "providers.json"

    def _ensure_dir(self) -> None:
        """Ensure the config directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _load_all(self) -> dict[str, Any]:
        """Load all provider credentials."""
        if not self.providers_file.exists():
            return {}
        try:
            with self.providers_file.open(encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save_all(self, data: dict[str, Any]) -> None:
        """Save all provider credentials."""
        self._ensure_dir()
        with self.providers_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        # Set file permissions to owner-only (Unix)
        if os.name != "nt":
            os.chmod(self.providers_file, 0o600)

    def get(self, provider_id: str) -> OAuthCredentials | None:
        """Get credentials for a provider."""
        data = self._load_all()
        if provider_id not in data:
            return None
        return _opencode_format_to_esprit(data[provider_id])

    def set(self, provider_id: str, credentials: OAuthCredentials) -> None:
        """Store credentials for a provider."""
        data = self._load_all()
        data[provider_id] = _esprit_to_opencode_format(credentials)
        self._save_all(data)

    def delete(self, provider_id: str) -> bool:
        """Delete credentials for a provider."""
        data = self._load_all()
        if provider_id not in data:
            return False
        del data[provider_id]
        self._save_all(data)
        return True

    def list_providers(self) -> list[str]:
        """List all providers with stored credentials."""
        data = self._load_all()
        return list(data.keys())

    def has_credentials(self, provider_id: str) -> bool:
        """Check if credentials exist for a provider."""
        data = self._load_all()
        return provider_id in data

    def get_auth_type(self, provider_id: str) -> str | None:
        """Get the authentication type for a provider."""
        creds = self.get(provider_id)
        return creds.type if creds else None
