"""
LiteLLM integration for provider authentication.

This module provides a custom HTTP client that integrates provider OAuth
authentication with LiteLLM's completion calls.
"""

import asyncio
import logging
import os
from typing import Any

import httpx

from esprit.providers import get_provider_auth, PROVIDERS
from esprit.providers.base import OAuthCredentials
from esprit.providers.token_store import TokenStore

logger = logging.getLogger(__name__)


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
            - "github-copilot/gpt-5" -> "github-copilot"
            - "google/gemini-2.5-pro" -> "google"
        """
        model_lower = model_name.lower()

        # Check for explicit provider prefix
        if "/" in model_lower:
            prefix = model_lower.split("/")[0]
            # Bedrock uses AWS credentials, not OAuth - skip it
            if prefix == "bedrock":
                return None
            if prefix in PROVIDERS:
                return prefix

        # Detect from model name
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
        """Get credentials for a provider."""
        return self.token_store.get(provider_id)

    def has_oauth_credentials(self, provider_id: str) -> bool:
        """Check if OAuth credentials exist for a provider."""
        creds = self.get_credentials(provider_id)
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
        return None
    
    if credentials.type == "api":
        return credentials.access_token
    
    if credentials.type == "oauth":
        # For OAuth, we need to use the access token
        # But LiteLLM expects this in headers, not as api_key
        # Return a dummy key to prevent LiteLLM from erroring
        return "oauth-authenticated"
    
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
    if not credentials or credentials.type != "oauth":
        return {}
    
    provider = get_provider_auth(provider_id)
    if not provider:
        return {}
    
    # Get modified headers
    _, headers, _ = provider.modify_request("", {}, None, credentials)
    return headers


def should_use_oauth(model_name: str) -> bool:
    """Check if OAuth should be used for a model."""
    client = get_auth_client()
    provider_id = client.detect_provider(model_name)
    
    if not provider_id:
        return False
    
    return client.has_oauth_credentials(provider_id)


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
