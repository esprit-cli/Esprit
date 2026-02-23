import asyncio
from unittest.mock import MagicMock

from esprit.interface.launchpad import LaunchpadApp
from esprit.providers.base import AuthCallbackResult, OAuthCredentials


def test_provider_config_lists_esprit_with_platform_auth(monkeypatch) -> None:
    monkeypatch.setattr("esprit.auth.credentials.is_authenticated", lambda: True)

    app = LaunchpadApp()
    app._account_pool = MagicMock()
    app._token_store = MagicMock()
    app._account_pool.account_count.return_value = 0
    app._token_store.has_credentials.return_value = False

    entries = app._build_provider_entries()
    esprit_entry = next(entry for entry in entries if entry.key == "provider:esprit")

    assert esprit_entry.label.startswith("●")
    assert esprit_entry.hint == "connected"


def test_provider_actions_hide_api_key_for_esprit() -> None:
    app = LaunchpadApp()
    app._selected_provider_id = "esprit"

    entries = app._build_provider_action_entries()
    keys = [entry.key for entry in entries]

    assert "provider_oauth" in keys
    assert "provider_api_key" not in keys


def test_esprit_callback_does_not_persist_provider_token() -> None:
    app = LaunchpadApp()
    app._account_pool = MagicMock()
    app._token_store = MagicMock()
    app._set_status = MagicMock()
    app._set_view = MagicMock()

    callback_result = AuthCallbackResult(
        success=True,
        credentials=OAuthCredentials(type="oauth", access_token="token"),
    )

    asyncio.run(app._handle_provider_callback("esprit", callback_result))

    app._token_store.set.assert_not_called()
