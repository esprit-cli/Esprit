"""
Supabase authentication client for Esprit CLI.

Uses Device Authorization Flow (RFC 8628) for secure CLI authentication.
"""

from __future__ import annotations

import os
import time
import webbrowser
from dataclasses import dataclass
from datetime import timezone
from typing import Any

import requests

from esprit.auth.credentials import Credentials, save_credentials


# Configuration - can be overridden by environment variables
SUPABASE_URL = os.getenv("ESPRIT_SUPABASE_URL", "https://frzsqgyzuikwgqsrdkgz.supabase.co")
SUPABASE_ANON_KEY = os.getenv(
    "ESPRIT_SUPABASE_ANON_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZyenNxZ3l6dWlrd2dxc3Jka2d6Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjQxOTU5MDYsImV4cCI6MjA3OTc3MTkwNn0.ZRVsq1lCp8_HPy4EsljdYAn3GhqFfZ1yekQOV2d6KLQ",
)

# API URL for device flow endpoints
API_BASE_URL = os.getenv("ESPRIT_API_URL", "https://esprit.dev/api/v1")


@dataclass
class AuthResult:
    """Result of authentication attempt."""

    success: bool
    error: str | None = None
    credentials: Credentials | None = None


class SupabaseAuthClient:
    """Client for Supabase authentication using Device Flow."""

    def __init__(
        self,
        supabase_url: str = SUPABASE_URL,
        supabase_key: str = SUPABASE_ANON_KEY,
        api_base_url: str = API_BASE_URL,
    ) -> None:
        self.supabase_url = supabase_url.rstrip("/")
        self.supabase_key = supabase_key
        self.api_base_url = api_base_url.rstrip("/")
        self.headers = {
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        }

    def login_with_device_flow(self) -> AuthResult:
        """
        Initiate Device Authorization Flow (RFC 8628).

        This is a secure alternative to localhost callback OAuth:
        1. Request device code from server
        2. Display user code and verification URL
        3. Open browser for user to authenticate
        4. Poll server until user completes authorization
        5. Save credentials locally

        No local server required - much more secure!
        """
        from rich.console import Console

        console = Console()

        # Step 1: Request device code
        try:
            response = requests.post(
                f"{self.api_base_url}/auth/device/code",
                json={"client_id": "esprit-cli"},
                timeout=30,
            )

            if response.status_code != 200:
                return AuthResult(
                    success=False,
                    error=f"Failed to get device code: {response.text}",
                )

            data = response.json()
            device_code = data["device_code"]
            user_code = data["user_code"]
            verification_uri = data["verification_uri"]
            verification_uri_complete = data["verification_uri_complete"]
            expires_in = data["expires_in"]
            interval = data["interval"]

        except requests.RequestException as e:
            return AuthResult(success=False, error=f"Network error: {e}")

        # Step 2: Display code to user
        console.print()
        console.print("[bold]To complete login:[/]")
        console.print()
        console.print(f"  1. Go to: [cyan]{verification_uri}[/]")
        console.print(f"  2. Enter code: [bold yellow]{user_code}[/]")
        console.print()

        # Step 3: Open browser automatically
        console.print("[dim]Opening browser...[/]")
        webbrowser.open(verification_uri_complete)
        console.print()

        # Step 4: Poll for token
        console.print("[dim]Waiting for authorization...[/]", end="")

        start_time = time.time()
        while time.time() - start_time < expires_in:
            time.sleep(interval)

            try:
                token_response = requests.post(
                    f"{self.api_base_url}/auth/device/token",
                    json={
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    timeout=30,
                )

                if token_response.status_code == 200:
                    # Success!
                    token_data = token_response.json()
                    console.print(" [green]✓[/]")
                    return self._complete_device_login(token_data)

                # Check error type
                if token_response.status_code == 400:
                    error_data = token_response.json()
                    error = error_data.get("detail", {})

                    if isinstance(error, dict):
                        error_code = error.get("error", "")
                    else:
                        error_code = str(error)

                    if "authorization_pending" in error_code:
                        # Still waiting - print dot and continue
                        console.print(".", end="")
                        continue
                    elif "expired_token" in error_code:
                        console.print()
                        return AuthResult(
                            success=False,
                            error="Code expired. Please try again.",
                        )
                    else:
                        console.print()
                        return AuthResult(
                            success=False,
                            error=f"Authorization failed: {error_code}",
                        )

            except requests.RequestException:
                # Network error during polling - continue trying
                console.print(".", end="")
                continue

        console.print()
        return AuthResult(success=False, error="Authorization timed out. Please try again.")

    def _complete_device_login(self, token_data: dict[str, Any]) -> AuthResult:
        """Complete login after device authorization."""
        access_token = token_data["access_token"]
        user_id = token_data.get("user_id", "")
        email = token_data.get("email", "")
        full_name = token_data.get("full_name", "")
        plan = token_data.get("plan", "free")

        credentials: Credentials = {
            "access_token": access_token,
            "refresh_token": "",
            "expires_at": token_data.get("expires_in", 0) + int(time.time()),
            "user_id": user_id,
            "email": email,
            "full_name": full_name,
            "plan": plan,
        }

        save_credentials(credentials)

        return AuthResult(success=True, credentials=credentials)

    # Keep legacy method for backwards compatibility but mark as deprecated
    def login_with_oauth(self, provider: str = "github") -> AuthResult:
        """
        Legacy OAuth login - redirects to device flow.

        DEPRECATED: Use login_with_device_flow() instead.
        """
        return self.login_with_device_flow()

    def login_with_email(self, email: str, password: str) -> AuthResult:
        """Login with email and password."""
        url = f"{self.supabase_url}/auth/v1/token?grant_type=password"

        try:
            response = requests.post(
                url,
                headers=self.headers,
                json={"email": email, "password": password},
                timeout=30,
            )

            if response.status_code != 200:
                error_data = response.json()
                return AuthResult(
                    success=False,
                    error=error_data.get("error_description", "Login failed"),
                )

            data = response.json()
            return self._complete_login(data["access_token"], data.get("refresh_token"))

        except requests.RequestException as e:
            return AuthResult(success=False, error=str(e))

    def _complete_login(
        self,
        access_token: str,
        refresh_token: str | None = None,
    ) -> AuthResult:
        """Complete login by fetching user info and saving credentials."""
        # Get user info
        user_info = self._get_user_info(access_token)

        if not user_info:
            return AuthResult(success=False, error="Failed to get user info")

        # Get profile info (plan, etc.)
        profile = self._get_user_profile(access_token, user_info["id"])

        credentials: Credentials = {
            "access_token": access_token,
            "refresh_token": refresh_token or "",
            "expires_at": user_info.get("expires_at", 0),
            "user_id": user_info["id"],
            "email": user_info.get("email", ""),
            "full_name": user_info.get("user_metadata", {}).get("full_name"),
            "plan": profile.get("plan", "free") if profile else "free",
        }

        save_credentials(credentials)

        return AuthResult(success=True, credentials=credentials)

    def _get_user_info(self, access_token: str) -> dict[str, Any] | None:
        """Get user info from Supabase."""
        url = f"{self.supabase_url}/auth/v1/user"
        headers = {
            **self.headers,
            "Authorization": f"Bearer {access_token}",
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.json()
        except requests.RequestException:
            pass

        return None

    def _get_user_profile(
        self,
        access_token: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        """Get user profile from profiles table."""
        url = f"{self.supabase_url}/rest/v1/profiles?id=eq.{user_id}&select=*"
        headers = {
            **self.headers,
            "Authorization": f"Bearer {access_token}",
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data[0] if data else None
        except requests.RequestException:
            pass

        return None

    def get_usage(self, access_token: str, user_id: str) -> dict[str, Any] | None:
        """Get user's current usage stats."""
        from datetime import datetime

        current_month = datetime.now(tz=timezone.utc).strftime("%Y-%m")

        url = (
            f"{self.supabase_url}/rest/v1/usage?"
            f"user_id=eq.{user_id}&month=eq.{current_month}&select=*"
        )
        headers = {
            **self.headers,
            "Authorization": f"Bearer {access_token}",
        }

        try:
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                data = response.json()
                return data[0] if data else {"scans_count": 0, "tokens_used": 0}
        except requests.RequestException:
            pass

        return None
