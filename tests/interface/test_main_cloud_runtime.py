from importlib import import_module
from unittest.mock import patch


interface_main = import_module("esprit.interface.main")


def test_should_use_cloud_runtime_for_verified_free_plan() -> None:
    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=True),
        patch("esprit.interface.main.Config.get", return_value="esprit/default"),
        patch(
            "esprit.auth.credentials.verify_subscription",
            return_value={"valid": True, "cloud_enabled": True, "plan": "free"},
        ),
    ):
        assert interface_main._should_use_cloud_runtime() is True


def test_should_not_use_cloud_runtime_when_verification_invalid() -> None:
    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=True),
        patch("esprit.interface.main.Config.get", return_value="esprit/default"),
        patch(
            "esprit.auth.credentials.verify_subscription",
            return_value={"valid": False, "cloud_enabled": True},
        ),
    ):
        assert interface_main._should_use_cloud_runtime() is False


def test_should_not_use_cloud_runtime_when_cloud_disabled() -> None:
    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=True),
        patch("esprit.interface.main.Config.get", return_value="esprit/default"),
        patch(
            "esprit.auth.credentials.verify_subscription",
            return_value={"valid": True, "cloud_enabled": False},
        ),
    ):
        assert interface_main._should_use_cloud_runtime() is False


def test_should_not_use_cloud_runtime_for_non_cloud_model() -> None:
    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=True),
        patch("esprit.interface.main.Config.get", return_value="openai/gpt-5"),
    ):
        assert interface_main._should_use_cloud_runtime() is False
