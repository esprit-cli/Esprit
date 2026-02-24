from importlib import import_module
from unittest.mock import MagicMock, patch

interface_main = import_module("esprit.interface.main")


def test_ensure_provider_configured_accepts_public_opencode_model() -> None:
    with (
        patch("esprit.providers.token_store.TokenStore") as token_store_cls,
        patch("esprit.providers.account_pool.get_account_pool") as get_pool,
        patch("esprit.auth.credentials.is_authenticated", return_value=False),
        patch(
            "esprit.interface.main.Config.get",
            side_effect=lambda name: "opencode/minimax-m2.5-free" if name == "esprit_llm" else None,
        ),
    ):
        token_store = MagicMock()
        token_store.has_credentials.return_value = False
        token_store_cls.return_value = token_store

        pool = MagicMock()
        pool.has_accounts.return_value = False
        get_pool.return_value = pool

        assert interface_main.ensure_provider_configured() is True


def test_get_available_models_limits_opencode_to_public_without_api_key() -> None:
    with patch("esprit.providers.token_store.TokenStore") as token_store_cls:
        token_store = MagicMock()
        token_store.has_credentials.return_value = False
        token_store_cls.return_value = token_store

        models = interface_main._get_available_models([("opencode", "Public models (no auth)")])

    model_ids = [model_id for model_id, _ in models]
    assert "opencode/minimax-m2.5-free" in model_ids
    assert "opencode/gpt-5.2-codex" not in model_ids
