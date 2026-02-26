import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_provider_config_marks_opencode_public_when_no_api_key(monkeypatch) -> None:
    monkeypatch.setattr("esprit.auth.credentials.is_authenticated", lambda: False)

    app = LaunchpadApp()
    app._account_pool = MagicMock()
    app._token_store = MagicMock()
    app._account_pool.account_count.return_value = 0
    app._token_store.has_credentials.return_value = False

    entries = app._build_provider_entries()
    opencode_entry = next(entry for entry in entries if entry.key == "provider:opencode")

    assert opencode_entry.label.startswith("●")
    assert opencode_entry.hint == "public models (no auth)"


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


def test_wizard_welcome_entries_have_two_options() -> None:
    entries = LaunchpadApp._build_wizard_welcome_entries()

    assert len(entries) == 2
    assert entries[0].key == "wizard_cloud"
    assert entries[0].label == "Log in with Esprit (Recommended)"
    assert entries[1].key == "wizard_local"
    assert entries[1].label == "Bring your own model (connectors)"


def test_wizard_cloud_triggers_immediate_oauth() -> None:
    app = LaunchpadApp()
    app._connect_selected_provider = AsyncMock()

    entry = next(e for e in app._build_wizard_welcome_entries() if e.key == "wizard_cloud")
    asyncio.run(app._activate_entry(entry))

    assert app._selected_provider_id == "esprit"
    app._connect_selected_provider.assert_awaited_once()


def test_wizard_local_marks_done_and_routes_to_provider() -> None:
    app = LaunchpadApp()
    app._wizard_mode = True
    app._mark_wizard_done = MagicMock()
    app._set_view = MagicMock()

    entry = next(e for e in app._build_wizard_welcome_entries() if e.key == "wizard_local")
    asyncio.run(app._activate_entry(entry))

    app._mark_wizard_done.assert_called_once()
    assert app._wizard_mode is False
    app._set_view.assert_called_once_with("provider")


def test_wizard_auth_failure_returns_to_welcome() -> None:
    app = LaunchpadApp()
    app._wizard_mode = True
    app._set_status = MagicMock()
    app._set_view = MagicMock()

    callback_result = AuthCallbackResult(success=False, error="denied")
    asyncio.run(app._handle_provider_callback("esprit", callback_result))

    app._set_view.assert_called_once_with("wizard_welcome", push=False)


def test_wizard_auth_success_sets_default_esprit_model() -> None:
    app = LaunchpadApp()
    app._wizard_mode = True
    app._account_pool = MagicMock()
    app._token_store = MagicMock()
    app._set_status = MagicMock()
    app._set_view = MagicMock()
    app._mark_wizard_done = MagicMock()

    callback_result = AuthCallbackResult(
        success=True,
        credentials=OAuthCredentials(type="oauth", access_token="token"),
    )

    with (
        patch("esprit.interface.launchpad.Config.get", return_value=None),
        patch("esprit.interface.launchpad.Config.save_current", return_value=True) as save_current,
        patch.dict(os.environ, {}, clear=True),
    ):
        asyncio.run(app._handle_provider_callback("esprit", callback_result))

    assert os.environ["ESPRIT_LLM"] == "esprit/default"
    save_current.assert_called_once()
    app._mark_wizard_done.assert_called_once()
    assert app._wizard_mode is False
    app._set_view.assert_called_once_with("scan_choose", push=False)
