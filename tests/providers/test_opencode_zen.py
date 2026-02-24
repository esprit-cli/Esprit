"""Tests for OpenCode Zen provider."""

import pytest

from esprit.providers.base import AuthMethod, OAuthCredentials
from esprit.providers.opencode_zen import OPENCODE_ZEN_AUTH_URL, OpenCodeZenProvider


@pytest.mark.asyncio
async def test_authorize_uses_code_flow() -> None:
    provider = OpenCodeZenProvider()

    result = await provider.authorize()

    assert result.url == OPENCODE_ZEN_AUTH_URL
    assert result.method == AuthMethod.CODE


@pytest.mark.asyncio
async def test_callback_accepts_api_key() -> None:
    provider = OpenCodeZenProvider()
    auth_result = await provider.authorize()

    result = await provider.callback(auth_result, "test-opencode-token")

    assert result.success is True
    assert result.credentials is not None
    assert result.credentials.type == "api"
    assert result.credentials.access_token == "test-opencode-token"


@pytest.mark.asyncio
async def test_callback_rejects_empty_key() -> None:
    provider = OpenCodeZenProvider()
    auth_result = await provider.authorize()

    result = await provider.callback(auth_result, "")

    assert result.success is False
    assert result.error is not None


def test_modify_request_sets_bearer_token() -> None:
    provider = OpenCodeZenProvider()
    creds = OAuthCredentials(type="api", access_token="test-opencode-token")

    _, headers, _ = provider.modify_request(
        "https://opencode.ai/zen/v1/chat/completions",
        {"Content-Type": "application/json"},
        {},
        creds,
    )

    assert headers["Authorization"] == "Bearer test-opencode-token"
