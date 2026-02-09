"""
Base classes for provider authentication.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AuthMethod(str, Enum):
    """Authentication flow method."""
    CODE = "code"  # User pastes authorization code
    AUTO = "auto"  # Device flow with polling


@dataclass
class OAuthCredentials:
    """OAuth credential storage structure."""
    type: str = "oauth"  # "oauth" or "api"
    access_token: str | None = None
    refresh_token: str | None = None
    expires_at: int | None = None  # Unix timestamp in milliseconds
    account_id: str | None = None
    enterprise_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def is_expired(self) -> bool:
        """Check if the access token is expired."""
        if not self.expires_at:
            return False
        import time
        # Add 5 minute buffer for safety
        return time.time() * 1000 >= (self.expires_at - 300_000)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "type": self.type,
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "account_id": self.account_id,
            "enterprise_url": self.enterprise_url,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OAuthCredentials":
        """Create from dictionary."""
        return cls(
            type=data.get("type", "oauth"),
            access_token=data.get("access_token"),
            refresh_token=data.get("refresh_token"),
            expires_at=data.get("expires_at"),
            account_id=data.get("account_id"),
            enterprise_url=data.get("enterprise_url"),
            extra=data.get("extra", {}),
        )


@dataclass
class AuthorizationResult:
    """Result from starting authorization flow."""
    url: str
    instructions: str
    method: AuthMethod
    verifier: str | None = None  # For PKCE
    device_code: str | None = None  # For device flow
    interval: int = 5  # Polling interval in seconds


@dataclass
class AuthCallbackResult:
    """Result from completing authorization."""
    success: bool
    credentials: OAuthCredentials | None = None
    error: str | None = None


class ProviderAuth(ABC):
    """Base class for provider authentication plugins."""

    provider_id: str
    display_name: str

    @abstractmethod
    async def authorize(self, **kwargs) -> AuthorizationResult:
        """
        Start the authorization flow.
        
        Returns authorization info including URL to open and instructions.
        """
        pass

    @abstractmethod
    async def callback(
        self,
        auth_result: AuthorizationResult,
        code: str | None = None,
    ) -> AuthCallbackResult:
        """
        Complete the authorization flow.
        
        For CODE method: code parameter contains the authorization code.
        For AUTO method: polls until authorization completes.
        """
        pass

    @abstractmethod
    async def refresh_token(
        self,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """
        Refresh an expired access token.
        
        Returns updated credentials with new access token.
        """
        pass

    @abstractmethod
    def modify_request(
        self,
        url: str,
        headers: dict[str, str],
        body: Any,
        credentials: OAuthCredentials,
    ) -> tuple[str, dict[str, str], Any]:
        """
        Modify an API request for authenticated access.
        
        Returns (modified_url, modified_headers, modified_body).
        """
        pass

    def get_auth_methods(self) -> list[dict[str, str]]:
        """Get available authentication methods for this provider."""
        return [
            {"type": "oauth", "label": f"Login with {self.display_name}"},
            {"type": "api", "label": "Enter API Key manually"},
        ]

    async def ensure_valid_token(
        self,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """Ensure the access token is valid, refreshing if needed."""
        if credentials.type != "oauth":
            return credentials
        if credentials.is_expired():
            return await self.refresh_token(credentials)
        return credentials
