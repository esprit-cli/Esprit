"""Tests for esprit.auth.credentials helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from esprit.auth import credentials


@pytest.fixture(autouse=True)
def _reset_verify_cache() -> None:
    credentials._verification_cache["token"] = None
    credentials._verification_cache["checked_at"] = 0.0
    credentials._verification_cache["result"] = None
    yield
    credentials._verification_cache["token"] = None
    credentials._verification_cache["checked_at"] = 0.0
    credentials._verification_cache["result"] = None


def test_verify_subscription_requires_token() -> None:
    with patch("esprit.auth.credentials.get_auth_token", return_value=None):
        result = credentials.verify_subscription()

    assert result["valid"] is False
    assert result["cloud_enabled"] is False
    assert "No authentication token" in result["error"]


def test_verify_subscription_success() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "valid": True,
        "plan": "pro",
        "quota_remaining": {"scans": 42, "tokens": 9000000},
        "cloud_enabled": True,
        "available_models": ["default", "haiku"],
    }

    with patch("esprit.auth.credentials.requests.get", return_value=response):
        result = credentials.verify_subscription(access_token="token-123")

    assert result["valid"] is True
    assert result["plan"] == "pro"
    assert result["cloud_enabled"] is True
    assert result["quota_remaining"]["scans"] == 42
    assert "haiku" in result["available_models"]


def test_verify_subscription_http_error_returns_invalid() -> None:
    response = MagicMock()
    response.status_code = 401
    response.json.return_value = {}

    with patch("esprit.auth.credentials.requests.get", return_value=response):
        result = credentials.verify_subscription(access_token="token-123")

    assert result["valid"] is False
    assert "HTTP 401" in result["error"]


def test_verify_subscription_uses_cache() -> None:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "valid": True,
        "plan": "team",
        "quota_remaining": {"scans": 10, "tokens": 500},
        "cloud_enabled": True,
        "available_models": ["default"],
    }

    with patch("esprit.auth.credentials.requests.get", return_value=response) as mock_get:
        first = credentials.verify_subscription(access_token="token-123")
        second = credentials.verify_subscription(access_token="token-123")

    assert first["valid"] is True
    assert second["valid"] is True
    mock_get.assert_called_once()


def test_verify_subscription_invalid_api_url_returns_error() -> None:
    with patch.dict("os.environ", {"ESPRIT_API_URL": "http://evil.example"}, clear=True):
        result = credentials.verify_subscription(access_token="token-123", force_refresh=True)

    assert result["valid"] is False
    assert "HTTPS" in result["error"]
