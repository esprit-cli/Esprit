"""
Configuration management for Esprit CLI.

Stores user preferences like default model, etc.
"""

import json
import os
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.prompt import Prompt
from rich.table import Table

console = Console()

# Available models by provider
AVAILABLE_MODELS = {
    "esprit": [
        ("default", "Esprit Default"),
        ("kimi-k2.5", "Esprit Pro"),
        ("haiku", "Esprit Fast"),
    ],
    "openai": [
        ("gpt-5.3-codex", "GPT-5.3 Codex (recommended)"),
        ("gpt-5.1-codex", "GPT-5.1 Codex"),
        ("gpt-5.1-codex-max", "GPT-5.1 Codex Max (maximum context)"),
        ("gpt-5.1-codex-mini", "GPT-5.1 Codex Mini (faster)"),
        ("codex-mini-latest", "Codex Mini (faster, lightweight)"),
        ("gpt-5.2", "GPT-5.2"),
        ("gpt-5.2-codex", "GPT-5.2 Codex"),
    ],
    "anthropic": [
        ("claude-sonnet-4-5-20250514", "Claude Sonnet 4.5 (recommended)"),
        ("claude-opus-4-5-20251101", "Claude Opus 4.5 (advanced reasoning)"),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5 (faster)"),
    ],
    "github-copilot": [
        ("gpt-5", "GPT-5 (via Copilot)"),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5 (via Copilot)"),
    ],
    "google": [
        ("gemini-3-pro", "Gemini 3 Pro (recommended)"),
        ("gemini-3-flash", "Gemini 3 Flash (faster)"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ],
    "antigravity": [
        ("claude-opus-4-6-thinking", "Claude Opus 4.6 Thinking (free)"),
        ("claude-opus-4-5-thinking", "Claude Opus 4.5 Thinking (free)"),
        ("claude-sonnet-4-5-thinking", "Claude Sonnet 4.5 Thinking (free)"),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5 (free)"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash (free)"),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite (free)"),
        ("gemini-2.5-flash-thinking", "Gemini 2.5 Flash Thinking (free)"),
        ("gemini-2.5-pro", "Gemini 2.5 Pro (free)"),
        ("gemini-3-flash", "Gemini 3 Flash (free)"),
        ("gemini-3-pro-high", "Gemini 3 Pro High (free)"),
        ("gemini-3-pro-image", "Gemini 3 Pro Image (free)"),
        ("gemini-3-pro-low", "Gemini 3 Pro Low (free)"),
    ],
}


class Config:
    """Configuration storage for Esprit CLI."""

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or Path.home() / ".esprit"
        self.config_file = self.config_dir / "config.json"

    def _ensure_dir(self) -> None:
        """Ensure the config directory exists."""
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        """Load configuration."""
        if not self.config_file.exists():
            return {}
        try:
            with self.config_file.open(encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, data: dict[str, Any]) -> None:
        """Save configuration."""
        self._ensure_dir()
        with self.config_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        data = self._load()
        return data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set a configuration value."""
        data = self._load()
        data[key] = value
        self._save(data)

    def get_model(self) -> str | None:
        """Get the configured model."""
        # Environment variable takes precedence
        env_model = os.getenv("ESPRIT_LLM")
        if env_model:
            return env_model
        return self.get("model")

    def set_model(self, model: str) -> None:
        """Set the default model."""
        self.set("model", model)


def get_config() -> Config:
    """Get the global config instance."""
    return Config()


def cmd_config_model(model: str | None = None) -> int:
    """Configure the default LLM model."""
    from esprit.providers.token_store import TokenStore
    from esprit.providers.account_pool import get_account_pool

    token_store = TokenStore()
    pool = get_account_pool()
    config = Config()

    from esprit.providers.constants import MULTI_ACCOUNT_PROVIDERS as _multi_account

    # If no model specified, show interactive menu
    if not model:
        console.print()
        console.print("[bold]Select a model to use:[/]")
        console.print()

        # Group by provider — show connected first, then disconnected
        available_options = []
        option_num = 1
        connected_providers = []
        disconnected_providers = []

        for provider_id, models in AVAILABLE_MODELS.items():
            if provider_id in _multi_account:
                has_creds = pool.has_accounts(provider_id)
            elif provider_id == "esprit":
                # Esprit subscription uses platform credentials, not token store
                from esprit.auth.credentials import is_authenticated
                has_creds = is_authenticated()
            else:
                has_creds = token_store.has_credentials(provider_id)
            if has_creds:
                connected_providers.append((provider_id, models))
            else:
                disconnected_providers.append((provider_id, models))

        # Show connected providers first
        for provider_id, models in connected_providers:
            if provider_id == "esprit":
                auth_type = "PLATFORM"
            else:
                creds = token_store.get(provider_id)
                auth_type = creds.type.upper() if creds else "OAUTH"
            provider_label = {
                "esprit": "ESPRIT (YOUR SUBSCRIPTION)",
                "antigravity": "ANTIGRAVITY",
                "openai": "OPENAI",
                "anthropic": "ANTHROPIC",
                "google": "GOOGLE",
                "github-copilot": "GITHUB COPILOT",
            }.get(provider_id, provider_id.upper())
            console.print(f"  [bold green]●[/] [bold cyan]{provider_label}[/] [dim]({auth_type} connected)[/]")
            for model_id, model_name in models:
                full_model = f"{provider_id}/{model_id}"
                available_options.append(full_model)
                console.print(f"    [bold]{option_num}.[/] {model_name} [dim]({model_id})[/]")
                option_num += 1
            console.print()

        # Show disconnected providers (greyed out)
        if disconnected_providers:
            for provider_id, models in disconnected_providers:
                provider_label = {
                    "esprit": "ESPRIT (YOUR SUBSCRIPTION)",
                    "antigravity": "ANTIGRAVITY",
                    "openai": "OPENAI",
                    "anthropic": "ANTHROPIC",
                    "google": "GOOGLE",
                    "github-copilot": "GITHUB COPILOT",
                }.get(provider_id, provider_id.upper())
                console.print(f"  [dim]○ {provider_label} (not connected)[/]")
                for model_id, model_name in models:
                    console.print(f"    [dim]  {model_name}[/]")
            console.print()

        if not available_options:
            console.print("[yellow]No providers configured.[/]")
            console.print()
            console.print("Run 'esprit provider login' to authenticate with a provider.")
            console.print()
            return 1

        choice = Prompt.ask(
            "Enter number",
            choices=[str(i) for i in range(1, len(available_options) + 1)],
        )
        model = available_options[int(choice) - 1]

    # Validate model format
    if "/" not in model:
        # Try to infer provider
        for provider_id, models in AVAILABLE_MODELS.items():
            for model_id, _ in models:
                if model_id == model:
                    model = f"{provider_id}/{model_id}"
                    break

    config.set_model(model)

    console.print()
    console.print(f"[green]✓ Default model set to: {model}[/]")
    console.print()
    console.print("[dim]This will be used when running 'esprit local'[/]")
    console.print("[dim]Override with ESPRIT_LLM environment variable[/]")
    console.print()

    return 0


def cmd_config_show() -> int:
    """Show current configuration."""
    config = Config()

    console.print()
    console.print("[bold]Current Configuration[/]")
    console.print()

    table = Table(show_header=True, header_style="bold")
    table.add_column("Setting")
    table.add_column("Value")
    table.add_column("Source")

    # Model
    env_model = os.getenv("ESPRIT_LLM")
    config_model = config.get("model")
    if env_model:
        table.add_row("Model", env_model, "[cyan]ESPRIT_LLM env[/]")
    elif config_model:
        table.add_row("Model", config_model, "[dim]~/.esprit/config.json[/]")
    else:
        from esprit.llm.config import DEFAULT_MODEL
        default_display = DEFAULT_MODEL.replace("bedrock/", "").replace("us.anthropic.", "anthropic/")
        table.add_row("Model", f"[dim]{default_display} (default)[/]", "[dim]default[/]")

    console.print(table)
    console.print()

    return 0
