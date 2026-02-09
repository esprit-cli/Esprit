"""
CLI commands for provider authentication.
"""

import asyncio
import webbrowser
from typing import NoReturn

from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.prompt import Prompt, Confirm
from rich.table import Table

from esprit.providers import (
    PROVIDERS,
    PROVIDER_NAMES,
    get_provider_auth,
    list_providers,
)
from esprit.providers.base import AuthMethod, OAuthCredentials
from esprit.providers.token_store import TokenStore

console = Console()


def cmd_provider_login(provider_id: str | None = None) -> int:
    """Login to a provider via OAuth."""
    return asyncio.run(_provider_login(provider_id))


async def _provider_login(provider_id: str | None = None) -> int:
    """Async implementation of provider login."""
    token_store = TokenStore()

    # If no provider specified, show selection menu
    if not provider_id:
        console.print()
        console.print("[bold]Select a provider to login:[/]")
        console.print()
        
        providers = list_providers()
        for i, pid in enumerate(providers, 1):
            name = PROVIDER_NAMES.get(pid, pid)
            status = "✓" if token_store.has_credentials(pid) else " "
            console.print(f"  [{status}] {i}. {name}")
        
        console.print()
        choice = Prompt.ask(
            "Enter number",
            choices=[str(i) for i in range(1, len(providers) + 1)],
        )
        provider_id = providers[int(choice) - 1]

    # Get provider
    provider = get_provider_auth(provider_id)
    if not provider:
        console.print(f"[red]Unknown provider: {provider_id}[/]")
        return 1

    display_name = PROVIDER_NAMES.get(provider_id, provider_id)

    try:
        console.print()
        console.print(f"[bold cyan]🔐 Logging in to {display_name}[/]")
        console.print()

        # Handle enterprise URL for Copilot
        kwargs = {}
        if provider_id == "github-copilot":
            if Confirm.ask("Are you using GitHub Enterprise?", default=False):
                enterprise_url = Prompt.ask("Enter your GitHub Enterprise URL")
                kwargs["enterprise_url"] = enterprise_url
                provider_id = "github-copilot-enterprise"

        # Start authorization
        auth_result = await provider.authorize(**kwargs)

        # Open browser
        console.print(f"[dim]Opening browser to:[/]")
        console.print(f"  {auth_result.url}")
        console.print()
        
        try:
            webbrowser.open(auth_result.url)
        except Exception:
            console.print("[yellow]Could not open browser automatically.[/]")
            console.print("Please open the URL above manually.")
        
        console.print(f"[bold]{auth_result.instructions}[/]")
        console.print()

        # Handle callback based on method
        if auth_result.method == AuthMethod.CODE:
            # User needs to paste code
            code = Prompt.ask("Authorization code")
            callback_result = await provider.callback(auth_result, code)
        else:
            # Device flow - poll automatically
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Waiting for authorization...", total=None)
                callback_result = await provider.callback(auth_result)
                progress.update(task, description="Authorization complete")

        if not callback_result.success:
            console.print()
            console.print(f"[red]Login failed: {callback_result.error}[/]")
            return 1

        # Save credentials
        if callback_result.credentials:
            token_store.set(provider_id, callback_result.credentials)

        console.print()
        console.print(f"[green]✓ Successfully logged in to {display_name}[/]")
        console.print()
        return 0

    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]Login cancelled.[/]")
        return 1
    except Exception as e:
        console.print()
        console.print(f"[red]Login failed: {e}[/]")
        return 1


def cmd_provider_logout(provider_id: str | None = None) -> int:
    """Logout from a provider."""
    token_store = TokenStore()

    # If no provider specified, show selection menu
    if not provider_id:
        logged_in = [p for p in list_providers() if token_store.has_credentials(p)]
        
        if not logged_in:
            console.print()
            console.print("[dim]Not logged in to any providers.[/]")
            console.print()
            return 0

        console.print()
        console.print("[bold]Select a provider to logout:[/]")
        console.print()
        
        for i, pid in enumerate(logged_in, 1):
            name = PROVIDER_NAMES.get(pid, pid)
            console.print(f"  {i}. {name}")
        
        console.print()
        choice = Prompt.ask(
            "Enter number",
            choices=[str(i) for i in range(1, len(logged_in) + 1)],
        )
        provider_id = logged_in[int(choice) - 1]

    display_name = PROVIDER_NAMES.get(provider_id, provider_id)

    if not token_store.has_credentials(provider_id):
        console.print()
        console.print(f"[dim]Not logged in to {display_name}.[/]")
        console.print()
        return 0

    token_store.delete(provider_id)
    console.print()
    console.print(f"[green]✓ Logged out from {display_name}[/]")
    console.print()
    return 0


def cmd_provider_status() -> int:
    """Show provider authentication status."""
    token_store = TokenStore()
    
    console.print()
    console.print("[bold]Provider Authentication Status[/]")
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Provider")
    table.add_column("Status")
    table.add_column("Type")

    for provider_id in list_providers():
        name = PROVIDER_NAMES.get(provider_id, provider_id)
        creds = token_store.get(provider_id)
        
        if creds:
            status = "[green]✓ Logged in[/]"
            auth_type = creds.type.upper()
            if creds.type == "oauth" and creds.is_expired():
                status = "[yellow]⚠ Token expired[/]"
        else:
            status = "[dim]Not configured[/]"
            auth_type = "-"
        
        table.add_row(name, status, auth_type)

    console.print(table)
    console.print()
    
    console.print("[dim]Use 'esprit provider login' to authenticate with a provider.[/]")
    console.print()
    return 0


def cmd_provider_set_api_key(provider_id: str | None = None) -> int:
    """Set an API key for a provider."""
    token_store = TokenStore()

    # If no provider specified, show selection menu
    if not provider_id:
        console.print()
        console.print("[bold]Select a provider:[/]")
        console.print()
        
        providers = list_providers()
        for i, pid in enumerate(providers, 1):
            name = PROVIDER_NAMES.get(pid, pid)
            console.print(f"  {i}. {name}")
        
        console.print()
        choice = Prompt.ask(
            "Enter number",
            choices=[str(i) for i in range(1, len(providers) + 1)],
        )
        provider_id = providers[int(choice) - 1]

    display_name = PROVIDER_NAMES.get(provider_id, provider_id)

    console.print()
    api_key = Prompt.ask(f"Enter API key for {display_name}", password=True)
    
    if not api_key:
        console.print("[red]API key is required.[/]")
        return 1

    credentials = OAuthCredentials(
        type="api",
        access_token=api_key,
    )
    token_store.set(provider_id, credentials)

    console.print()
    console.print(f"[green]✓ API key saved for {display_name}[/]")
    console.print()
    return 0
