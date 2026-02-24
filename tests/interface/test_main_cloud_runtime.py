"""Tests for cloud runtime selection logic in main interface."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from esprit.interface.main import _get_configured_providers, _should_use_cloud_runtime


def test_should_use_cloud_runtime_for_paid_esprit_model() -> None:
    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=True),
        patch("esprit.auth.credentials.get_user_plan", return_value="pro"),
        patch("esprit.config.config.Config.get", return_value="esprit/default"),
        patch(
            "esprit.auth.credentials.verify_subscription",
            return_value={"valid": True, "cloud_enabled": True, "plan": "pro"},
        ),
    ):
        assert _should_use_cloud_runtime() is True


def test_should_not_use_cloud_runtime_when_verify_fails() -> None:
    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=True),
        patch("esprit.auth.credentials.get_user_plan", return_value="team"),
        patch("esprit.config.config.Config.get", return_value="esprit/default"),
        patch(
            "esprit.auth.credentials.verify_subscription",
            return_value={
                "valid": False,
                "error": "Subscription verification failed: timeout",
            },
        ),
    ):
        assert _should_use_cloud_runtime() is False


def test_should_not_use_cloud_runtime_for_non_subscription_model() -> None:
    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=True),
        patch("esprit.auth.credentials.get_user_plan", return_value="pro"),
        patch("esprit.config.config.Config.get", return_value="openai/gpt-5"),
    ):
        assert _should_use_cloud_runtime() is False


def test_should_not_use_cloud_runtime_for_free_plan() -> None:
    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=True),
        patch("esprit.auth.credentials.get_user_plan", return_value="free"),
        patch("esprit.config.config.Config.get", return_value="esprit/default"),
    ):
        assert _should_use_cloud_runtime() is False


def test_get_configured_providers_includes_paid_esprit_subscription() -> None:
    fake_pool = MagicMock()
    fake_pool.has_accounts.return_value = False
    fake_token_store = MagicMock()
    fake_token_store.has_credentials.return_value = False

    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=True),
        patch("esprit.auth.credentials.get_user_plan", return_value="pro"),
        patch("esprit.auth.credentials.get_user_email", return_value="user@example.com"),
        patch("esprit.providers.token_store.TokenStore", return_value=fake_token_store),
        patch("esprit.providers.account_pool.get_account_pool", return_value=fake_pool),
        patch("esprit.config.config.Config.get", return_value=None),
    ):
        providers = _get_configured_providers()

    assert providers[0][0] == "esprit"
    assert "PRO" in providers[0][1]
