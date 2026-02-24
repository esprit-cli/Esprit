"""
LiteLLM integration for provider authentication.

This module provides a custom HTTP client that integrates provider OAuth
authentication with LiteLLM's completion calls.
"""

import asyncio
import json
import logging
import os
from typing import Any

import httpx

from esprit.providers import get_provider_auth, PROVIDERS
from esprit.providers.base import OAuthCredentials
from esprit.providers.config import is_public_opencode_model
from esprit.providers.token_store import TokenStore
from esprit.providers.account_pool import AccountPool, get_account_pool
from esprit.providers.constants import MULTI_ACCOUNT_PROVIDERS

logger = logging.getLogger(__name__)

# Providers that use the multi-account pool
_MULTI_ACCOUNT_PROVIDERS = MULTI_ACCOUNT_PROVIDERS


class ProviderAuthClient:
    """
    HTTP client that handles provider OAuth authentication.
    
    Integrates with LiteLLM by providing a custom fetch function that:
    1. Detects the provider from the model name
    2. Loads OAuth credentials if available
    3. Refreshes tokens if expired
    4. Modifies requests with proper auth headers
    """

    def __init__(self):
        self.token_store = TokenStore()
        self._http_client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=120)
        return self._http_client

    async def close(self):
        """Close the HTTP client."""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def detect_provider(self, model_name: str) -> str | None:
        """
        Detect the provider ID from a model name.

        Examples:
            - "anthropic/claude-sonnet-4" -> "anthropic"
            - "openai/gpt-5" -> "openai"
            - "antigravity/claude-opus-4-6-thinking" -> "antigravity"
            - "github-copilot/gpt-5" -> "github-copilot"
            - "google/gemini-2.5-pro" -> "google"
            - "opencode/gpt-5.1-codex" -> "opencode"
            - "zen/gpt-5.1-codex" -> "opencode"
            - "esprit/claude-haiku-4-5" -> "esprit"
        """
        model_lower = model_name.lower()

        # Check for explicit provider prefix
        if "/" in model_lower:
            prefix = model_lower.split("/")[0]
            # Bedrock uses AWS credentials, not OAuth - skip it
            if prefix == "bedrock":
                return None
            # Esprit subscription provider
            if prefix == "esprit":
                return "esprit"
            # Alias for OpenCode Zen
            if prefix == "zen":
                return "opencode"
            if prefix in PROVIDERS:
                return prefix

        # Detect from model name
        # Check if bare model name is an Antigravity model with active accounts
        try:
            from esprit.providers.antigravity import ANTIGRAVITY_MODELS
            bare = model_lower.split("/", 1)[-1]
            if bare in ANTIGRAVITY_MODELS and get_account_pool().has_accounts("antigravity"):
                return "antigravity"
        except ImportError:
            pass

        if "claude" in model_lower:
            return "anthropic"
        if "gemini" in model_lower:
            return "google"
        if "gpt" in model_lower or "o1" in model_lower or "o3" in model_lower or "codex" in model_lower:
            # Check if using Copilot
            if self.token_store.has_credentials("github-copilot"):
                return "github-copilot"
            return "openai"

        return None

    def get_credentials(self, provider_id: str) -> OAuthCredentials | None:
        """Get credentials for a provider, checking pool first for multi-account."""
        if provider_id == "esprit":
            # Esprit subscription uses platform credentials
            from esprit.providers.esprit_subs import _load_esprit_credentials
            return _load_esprit_credentials()
        if provider_id in _MULTI_ACCOUNT_PROVIDERS:
            pool = get_account_pool()
            acct = pool.get_best_account(provider_id)
            if acct:
                return acct.credentials
        return self.token_store.get(provider_id)

    def has_oauth_credentials(self, provider_id: str) -> bool:
        """Check if OAuth credentials exist for a provider."""
        if provider_id == "esprit":
            from esprit.auth.credentials import is_authenticated
            return is_authenticated()
        if provider_id in _MULTI_ACCOUNT_PROVIDERS:
            pool = get_account_pool()
            if pool.has_accounts(provider_id):
                return True
        creds = self.token_store.get(provider_id)
        return creds is not None and creds.type == "oauth"

    async def ensure_valid_credentials(
        self,
        provider_id: str,
        credentials: OAuthCredentials,
    ) -> OAuthCredentials:
        """Ensure credentials are valid, refreshing if needed."""
        if credentials.type != "oauth":
            return credentials

        if not credentials.is_expired():
            return credentials

        # Refresh token
        provider = get_provider_auth(provider_id)
        if not provider:
            return credentials

        try:
            new_credentials = await provider.refresh_token(credentials)
            # Save refreshed tokens to the correct store
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                pool = get_account_pool()
                email = credentials.extra.get("email", "") if credentials.extra else ""
                if email:
                    pool.update_credentials(provider_id, email, new_credentials)
                else:
                    # Fallback: find account by matching refresh_token or account_id
                    accounts = pool.list_accounts(provider_id)
                    matched = False
                    for acct in accounts:
                        if (
                            (credentials.refresh_token and acct.credentials.refresh_token == credentials.refresh_token)
                            or (credentials.account_id and acct.account_id == credentials.account_id)
                        ):
                            pool.update_credentials(provider_id, acct.email, new_credentials)
                            matched = True
                            break
                    if not matched:
                        logger.warning("Could not find account to update for %s", provider_id)
            elif provider_id == "esprit":
                # Esprit credentials live in platform credentials file,
                # not in the token store.  refresh_token() already reloaded
                # from disk, so nothing extra to persist here.
                pass
            else:
                self.token_store.set(provider_id, new_credentials)
            return new_credentials
        except Exception as e:
            logger.warning(f"Token refresh failed for {provider_id}: {e}")
            return credentials

    async def make_request(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: Any,
        model_name: str,
    ) -> httpx.Response:
        """
        Make an authenticated HTTP request.
        
        This method:
        1. Detects the provider from the model name
        2. Loads and validates OAuth credentials
        3. Modifies the request with provider-specific auth
        4. Executes the request
        """
        provider_id = self.detect_provider(model_name)
        
        if provider_id:
            credentials = self.get_credentials(provider_id)
            
            if credentials and credentials.type == "oauth":
                # Ensure credentials are valid
                credentials = await self.ensure_valid_credentials(provider_id, credentials)
                
                # Get provider and modify request
                provider = get_provider_auth(provider_id)
                if provider:
                    url, headers, body = provider.modify_request(
                        url, headers, body, credentials
                    )
        
        # Make the request
        client = await self._get_client()
        
        if method.upper() == "POST":
            response = await client.post(url, headers=headers, json=body)
        elif method.upper() == "GET":
            response = await client.get(url, headers=headers)
        else:
            response = await client.request(method, url, headers=headers, json=body)
        
        return response


# Global client instance
_auth_client: ProviderAuthClient | None = None


def get_auth_client() -> ProviderAuthClient:
    """Get the global provider auth client."""
    global _auth_client
    if _auth_client is None:
        _auth_client = ProviderAuthClient()
    return _auth_client


def get_provider_api_key(model_name: str) -> str | None:
    """
    Get API key for a model, checking OAuth credentials first.
    
    This function is designed to integrate with LiteLLM's api_key parameter.
    Returns the API key/token to use, or None to use environment variables.
    """
    client = get_auth_client()
    provider_id = client.detect_provider(model_name)
    
    if not provider_id:
        return None
    
    credentials = client.get_credentials(provider_id)
    if not credentials:
        # OpenCode public models can run without user credentials, but LiteLLM's
        # openai adapter requires api_key to be present.
        if provider_id == "opencode" and is_public_opencode_model(model_name):
            return "sk-opencode-public-noauth"
        return None
    
    if credentials.type == "api":
        return credentials.access_token
    
    if credentials.type == "oauth":
        # Return the actual access token so litellm sets the correct
        # Authorization header instead of a dummy value.
        return credentials.access_token
    
    return None


def get_provider_headers(model_name: str) -> dict[str, str]:
    """
    Get custom headers for a model based on OAuth credentials.
    
    This function returns headers that should be merged with LiteLLM's request.
    """
    client = get_auth_client()
    provider_id = client.detect_provider(model_name)
    
    if not provider_id:
        return {}
    
    credentials = client.get_credentials(provider_id)

    # OpenCode public models are no-auth. Keep LiteLLM happy with a placeholder
    # key, but clear Authorization so no bearer token is sent upstream.
    if (
        provider_id == "opencode"
        and not credentials
        and is_public_opencode_model(model_name)
    ):
        return {"Authorization": ""}

    if not credentials or credentials.type != "oauth":
        return {}
    
    provider = get_provider_auth(provider_id)
    if not provider:
        return {}
    
    # Get modified headers (exclude Authorization since litellm sets it via api_key)
    _, headers, _ = provider.modify_request("", {}, None, credentials)
    headers.pop("Authorization", None)
    return headers


def should_use_oauth(model_name: str) -> bool:
    """Check if OAuth should be used for a model."""
    client = get_auth_client()
    provider_id = client.detect_provider(model_name)

    if not provider_id:
        return False

    return client.has_oauth_credentials(provider_id)


def sync_codex_credentials_to_litellm(model_name: str) -> None:
    """Sync esprit's OAuth credentials to litellm's ChatGPT auth file.

    litellm's built-in ``chatgpt/`` provider reads tokens from
    ``~/.config/litellm/chatgpt/auth.json``.  This function copies esprit's
    stored OAuth credentials into that file so litellm can authenticate
    without a separate login.

    For multi-account providers, syncs the current active account.
    """
    client = get_auth_client()
    provider_id = client.detect_provider(model_name)
    if not provider_id:
        return

    # Esprit subscription uses platform credentials — never sync to litellm auth file
    if provider_id == "esprit":
        return

    credentials = client.get_credentials(provider_id)
    if not credentials or credentials.type != "oauth":
        return

    token_dir = os.environ.get(
        "CHATGPT_TOKEN_DIR",
        os.path.expanduser("~/.config/litellm/chatgpt"),
    )
    auth_file = os.path.join(
        token_dir,
        os.environ.get("CHATGPT_AUTH_FILE", "auth.json"),
    )

    os.makedirs(token_dir, exist_ok=True)

    auth_data = {
        "access_token": credentials.access_token,
        "refresh_token": credentials.refresh_token,
        "id_token": credentials.access_token,
        "expires_at": credentials.expires_at // 1000 if credentials.expires_at else None,
        "account_id": credentials.account_id,
    }
    try:
        with open(auth_file, "w") as f:
            json.dump(auth_data, f)
        if os.name != "nt":
            os.chmod(auth_file, 0o600)
    except OSError:
        logger.warning("Failed to sync credentials to litellm auth file")


def get_modified_url(model_name: str, url: str) -> str:
    """Get the modified URL for OAuth requests (e.g., Codex endpoint)."""
    client = get_auth_client()
    provider_id = client.detect_provider(model_name)
    
    if not provider_id:
        return url
    
    credentials = client.get_credentials(provider_id)
    if not credentials or credentials.type != "oauth":
        return url
    
    provider = get_provider_auth(provider_id)
    if not provider:
        return url
    
    modified_url, _, _ = provider.modify_request(url, {}, None, credentials)
    return modified_url
