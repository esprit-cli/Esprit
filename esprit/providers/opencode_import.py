"""
Import credentials from OpenCode.

This allows Esprit to use credentials that were set up in OpenCode,
so users don't have to authenticate twice.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from esprit.providers.base import OAuthCredentials
from esprit.providers.token_store import TokenStore

logger = logging.getLogger(__name__)

# OpenCode credential storage location (XDG spec)
OPENCODE_AUTH_FILE = Path.home() / ".local" / "share" / "opencode" / "auth.json"

# Provider ID mapping from OpenCode to Esprit
PROVIDER_MAPPING = {
    "anthropic": "anthropic",
    "openai": "openai",
    "codex": "openai",  # Codex is OpenAI's Codex
    "github-copilot": "github-copilot",
    "google": "google",
    "opencode": "opencode",  # OpenCode Zen
    "zen": "opencode",  # Alias used by some tools/configs
}


def get_opencode_auth_path() -> Path:
    """Get the OpenCode auth file path."""
    # Check XDG_DATA_HOME first
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        return Path(xdg_data) / "opencode" / "auth.json"
    return OPENCODE_AUTH_FILE


def load_opencode_credentials() -> dict[str, Any]:
    """Load credentials from OpenCode auth file."""
    auth_path = get_opencode_auth_path()

    if not auth_path.exists():
        return {}

    try:
        with auth_path.open(encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load OpenCode credentials: {e}")
        return {}


def convert_opencode_credential(
    _provider_id: str,
    opencode_cred: dict[str, Any]
) -> OAuthCredentials | None:
    """Convert OpenCode credential format to Esprit format."""
    cred_type = opencode_cred.get("type", "")

    if cred_type == "oauth":
        return OAuthCredentials(
            type="oauth",
            access_token=opencode_cred.get("access"),
            refresh_token=opencode_cred.get("refresh"),
            expires_at=opencode_cred.get("expires"),  # Already in ms
            account_id=opencode_cred.get("accountId"),
            enterprise_url=opencode_cred.get("enterpriseUrl"),
        )
    elif cred_type == "api":
        return OAuthCredentials(
            type="api",
            access_token=opencode_cred.get("key"),
        )
    elif cred_type == "wellknown":
        # Well-known credentials reference environment variables
        # We can try to get the value
        env_var = opencode_cred.get("key", "")
        token = opencode_cred.get("token") or os.environ.get(env_var, "")
        if token:
            return OAuthCredentials(
                type="api",
                access_token=token,
            )

    return None


def list_opencode_providers() -> list[dict[str, Any]]:
    """List providers available in OpenCode."""
    opencode_creds = load_opencode_credentials()

    providers = []
    for oc_provider, cred in opencode_creds.items():
        esprit_provider = PROVIDER_MAPPING.get(oc_provider)
        if esprit_provider:
            providers.append({
                "opencode_id": oc_provider,
                "esprit_id": esprit_provider,
                "type": cred.get("type", "unknown"),
                "has_credentials": bool(cred.get("access") or cred.get("key") or cred.get("refresh")),
            })

    return providers


def import_from_opencode(provider_id: str | None = None) -> dict[str, bool]:
    """
    Import credentials from OpenCode to Esprit.

    Args:
        provider_id: Specific provider to import, or None for all

    Returns:
        Dict of provider_id -> success status
    """
    opencode_creds = load_opencode_credentials()
    token_store = TokenStore()
    results = {}

    for oc_provider, cred in opencode_creds.items():
        esprit_provider = PROVIDER_MAPPING.get(oc_provider)

        if not esprit_provider:
            continue

        if provider_id and esprit_provider != provider_id:
            continue

        converted = convert_opencode_credential(oc_provider, cred)
        if converted:
            token_store.set(esprit_provider, converted)
            results[esprit_provider] = True
            logger.info(f"Imported {oc_provider} -> {esprit_provider}")
        else:
            results[esprit_provider] = False

    return results


def has_opencode_credentials() -> bool:
    """Check if OpenCode has any credentials stored."""
    return get_opencode_auth_path().exists()


def cmd_import_opencode(provider_id: str | None = None) -> int:
    """CLI command to import credentials from OpenCode."""
    from rich.console import Console
    from rich.table import Table

    console = Console()

    if not has_opencode_credentials():
        console.print()
        console.print("[yellow]No OpenCode credentials found.[/]")
        console.print("[dim]OpenCode stores credentials at ~/.local/share/opencode/auth.json[/]")
        console.print()
        return 1

    providers = list_opencode_providers()

    if not providers:
        console.print()
        console.print("[yellow]No compatible providers found in OpenCode.[/]")
        console.print()
        return 1

    console.print()
    console.print("[bold]Importing credentials from OpenCode...[/]")
    console.print()

    results = import_from_opencode(provider_id)

    table = Table(show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("Status")

    for pid, success in results.items():
        if success:
            table.add_row(pid, "[green]✓ Imported[/]")
        else:
            table.add_row(pid, "[red]✗ Failed[/]")

    console.print(table)
    console.print()

    if any(results.values()):
        console.print("[green]✓ Credentials imported successfully![/]")
        console.print("[dim]Run 'esprit provider status' to verify.[/]")

    console.print()
    return 0
