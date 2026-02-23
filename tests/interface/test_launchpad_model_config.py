from unittest.mock import MagicMock, patch

from esprit.interface.launchpad import LaunchpadApp


def test_model_config_shows_only_connected_providers() -> None:
    app = LaunchpadApp()

    app._account_pool = MagicMock()
    app._token_store = MagicMock()

    app._account_pool.has_accounts.side_effect = lambda provider_id: provider_id == "openai"
    app._token_store.has_credentials.side_effect = lambda provider_id: provider_id == "anthropic"

    with patch("esprit.auth.credentials.is_authenticated", return_value=False):
        entries = app._build_model_entries()
    keys = [entry.key for entry in entries]

    assert "separator:anthropic" in keys
    assert "separator:openai" in keys
    assert "separator:opencode" in keys
    assert "separator:google" not in keys
    assert "separator:antigravity" not in keys


def test_model_config_limits_public_opencode_to_no_auth_models() -> None:
    app = LaunchpadApp()

    app._account_pool = MagicMock()
    app._token_store = MagicMock()

    app._account_pool.has_accounts.return_value = False
    app._token_store.has_credentials.return_value = False

    with patch("esprit.auth.credentials.is_authenticated", return_value=False):
        entries = app._build_model_entries()

    opencode_keys = [entry.key for entry in entries if entry.key.startswith("model:opencode/")]
    assert "model:opencode/minimax-m2.5-free" in opencode_keys
    assert "model:opencode/gpt-5.2-codex" not in opencode_keys


def test_model_config_shows_empty_state_when_no_provider_connected() -> None:
    app = LaunchpadApp()

    app._account_pool = MagicMock()
    app._token_store = MagicMock()

    app._account_pool.has_accounts.return_value = False
    app._token_store.has_credentials.return_value = False

    with (
        patch("esprit.auth.credentials.is_authenticated", return_value=False),
        patch("esprit.interface.launchpad.get_public_opencode_models", return_value=set()),
    ):
        entries = app._build_model_entries()
    keys = [entry.key for entry in entries]

    assert "info:no_connected_providers" in keys
    assert keys[-1] == "back"
