"""
CLI commands for authentication.

Provides login, logout, whoami, and status commands.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from esprit.auth.client import SupabaseAuthClient
from esprit.auth.credentials import (
    clear_credentials,
    get_credentials,
    is_authenticated,
)


console = Console()


def cmd_login(provider: str = "github") -> int:
    """
    Login to Esprit using Device Authorization Flow.

    Displays a code for the user to enter at esprit.dev/device.
    """
    if is_authenticated():
        creds = get_credentials()
        email = creds.get("email", "Unknown") if creds else "Unknown"

        console.print()
        console.print(f"[yellow]Already logged in as[/] [bold]{email}[/]")
        console.print("[dim]Use 'esprit logout' to sign out first.[/]")
        console.print()
        return 0

    console.print()
    console.print("[bold cyan]🔐 Logging in to Esprit...[/]")

    # Use Device Authorization Flow (secure, no localhost server)
    client = SupabaseAuthClient()
    result = client.login_with_device_flow()

    if result.success:
        creds = result.credentials
        email = creds.get("email", "Unknown") if creds else "Unknown"
        plan = creds.get("plan", "free") if creds else "free"

        success_text = Text()
        success_text.append("✓ ", style="bold green")
        success_text.append("Successfully logged in!\n\n", style="green")
        success_text.append("Email: ", style="bold")
        success_text.append(f"{email}\n", style="white")
        success_text.append("Plan: ", style="bold")
        success_text.append(f"{plan.upper()}", style="cyan")

        panel = Panel(
            success_text,
            title="[bold green]🛡️  ESPRIT LOGIN",
            border_style="green",
            padding=(1, 2),
        )

        console.print(panel)
        console.print()
        return 0
    else:
        error_text = Text()
        error_text.append("✗ ", style="bold red")
        error_text.append("Login failed\n\n", style="red")
        error_text.append("Error: ", style="bold")
        error_text.append(f"{result.error}", style="white")

        panel = Panel(
            error_text,
            title="[bold red]🛡️  ESPRIT LOGIN ERROR",
            border_style="red",
            padding=(1, 2),
        )

        console.print(panel)
        console.print()
        return 1


def cmd_logout() -> int:
    """Logout from Esprit."""
    if not is_authenticated():
        console.print()
        console.print("[yellow]Not currently logged in.[/]")
        console.print()
        return 0

    creds = get_credentials()
    email = creds.get("email", "Unknown") if creds else "Unknown"

    clear_credentials()

    console.print()
    console.print(f"[green]✓[/] Logged out from [bold]{email}[/]")
    console.print()
    return 0


def cmd_whoami() -> int:
    """Show current user information."""
    if not is_authenticated():
        console.print()
        console.print("[yellow]Not logged in.[/]")
        console.print("[dim]Use 'esprit login' to sign in.[/]")
        console.print()
        return 1

    creds = get_credentials()
    if not creds:
        console.print("[red]Error reading credentials.[/]")
        return 1

    user_text = Text()
    user_text.append("👤 ", style="bold")
    user_text.append("User Information\n\n", style="bold white")

    if creds.get("full_name"):
        user_text.append("Name: ", style="bold")
        user_text.append(f"{creds['full_name']}\n", style="white")

    user_text.append("Email: ", style="bold")
    user_text.append(f"{creds.get('email', 'Unknown')}\n", style="white")

    user_text.append("User ID: ", style="bold")
    user_text.append(f"{creds.get('user_id', 'Unknown')}\n", style="dim white")

    user_text.append("Plan: ", style="bold")
    plan = creds.get("plan", "free").upper()
    plan_style = {
        "FREE": "white",
        "PRO": "yellow",
        "TEAM": "magenta",
    }.get(plan, "white")
    user_text.append(f"{plan}", style=f"bold {plan_style}")

    panel = Panel(
        user_text,
        title="[bold cyan]🛡️  ESPRIT",
        border_style="cyan",
        padding=(1, 2),
    )

    console.print()
    console.print(panel)
    console.print()
    return 0


def cmd_status() -> int:
    """Show account status and usage."""
    if not is_authenticated():
        console.print()
        console.print("[yellow]Not logged in.[/]")
        console.print("[dim]Use 'esprit login' to sign in.[/]")
        console.print()
        return 1

    creds = get_credentials()
    if not creds:
        console.print("[red]Error reading credentials.[/]")
        return 1

    # Get usage from API
    client = SupabaseAuthClient()
    usage = client.get_usage(
        creds.get("access_token", ""),
        creds.get("user_id", ""),
    )

    plan = creds.get("plan", "free").lower()

    # Plan limits
    limits = {
        "free": {"scans": 0, "tokens": 0},  # No free tier
        "pro": {"scans": 10, "tokens": 1_000_000},
        "team": {"scans": float("inf"), "tokens": 10_000_000},
    }

    plan_limits = limits.get(plan, limits["free"])

    scans_used = usage.get("scans_count", 0) if usage else 0
    tokens_used = usage.get("tokens_used", 0) if usage else 0

    scans_limit = plan_limits["scans"]
    tokens_limit = plan_limits["tokens"]

    # Build status display
    status_text = Text()
    status_text.append("📊 ", style="bold")
    status_text.append("Account Status\n\n", style="bold white")

    status_text.append("Email: ", style="bold")
    status_text.append(f"{creds.get('email', 'Unknown')}\n", style="white")

    status_text.append("Plan: ", style="bold")
    plan_display = plan.upper()
    plan_style = {"FREE": "white", "PRO": "yellow", "TEAM": "magenta"}.get(
        plan_display, "white"
    )
    status_text.append(f"{plan_display}\n\n", style=f"bold {plan_style}")

    # Usage table
    table = Table(show_header=True, header_style="bold", box=None)
    table.add_column("Resource", style="bold")
    table.add_column("Used", justify="right")
    table.add_column("Limit", justify="right")
    table.add_column("Remaining", justify="right")

    # Scans row
    scans_remaining = max(0, scans_limit - scans_used) if scans_limit != float("inf") else "∞"
    scans_limit_str = "Unlimited" if scans_limit == float("inf") else str(int(scans_limit))
    table.add_row(
        "Scans (this month)",
        str(scans_used),
        scans_limit_str,
        str(scans_remaining),
    )

    # Tokens row
    tokens_remaining = max(0, tokens_limit - tokens_used)
    table.add_row(
        "LLM Tokens",
        f"{tokens_used:,}",
        f"{int(tokens_limit):,}",
        f"{tokens_remaining:,}",
    )

    panel = Panel(
        status_text,
        title="[bold cyan]🛡️  ESPRIT STATUS",
        border_style="cyan",
        padding=(1, 2),
    )

    console.print()
    console.print(panel)
    console.print()
    console.print(table)
    console.print()

    return 0
