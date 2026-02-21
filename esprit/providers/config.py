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
# Format: (model_id, display_name, cost_per_1m_in, cost_per_1m_out, context_window)
# Costs in USD. 0.0 = included in subscription/free tier.

ModelEntry = tuple[str, str, float, float, int]

AVAILABLE_MODELS: dict[str, list[ModelEntry]] = {
    "esprit": [
        ("default", "Esprit Default", 0.0, 0.0, 200_000),
    ],
    "anthropic": [
        ("claude-sonnet-4-6", "Claude Sonnet 4.6", 3.0, 15.0, 200_000),
        ("claude-opus-4-6", "Claude Opus 4.6", 5.0, 25.0, 200_000),
        ("claude-haiku-4-5-20251001", "Claude Haiku 4.5", 1.0, 5.0, 200_000),
    ],
    "openai": [
        ("gpt-5.3-codex", "GPT-5.3 Codex", 2.0, 8.0, 256_000),
        ("gpt-5.1-codex", "GPT-5.1 Codex", 2.0, 8.0, 256_000),
        ("gpt-5.1-codex-max", "GPT-5.1 Codex Max", 2.0, 8.0, 1_000_000),
        ("gpt-5.1-codex-mini", "GPT-5.1 Codex Mini", 0.40, 1.60, 256_000),
        ("codex-mini-latest", "Codex Mini", 0.40, 1.60, 256_000),
        ("gpt-5.2", "GPT-5.2", 2.50, 10.0, 128_000),
        ("gpt-5.2-codex", "GPT-5.2 Codex", 2.50, 10.0, 256_000),
    ],
    "openrouter": [
        ("anthropic/claude-sonnet-4-5-20250514", "Claude Sonnet 4.5", 3.0, 15.0, 200_000),
        ("anthropic/claude-opus-4-5-20251101", "Claude Opus 4.5", 15.0, 75.0, 200_000),
        ("anthropic/claude-haiku-4-5-20251001", "Claude Haiku 4.5", 0.80, 4.0, 200_000),
        ("openai/gpt-4.1", "GPT-4.1", 2.0, 8.0, 1_047_576),
        ("openai/gpt-4.1-mini", "GPT-4.1 Mini", 0.40, 1.60, 1_047_576),
        ("openai/gpt-4.1-nano", "GPT-4.1 Nano", 0.10, 0.40, 1_047_576),
        ("openai/o4-mini", "o4-mini", 1.10, 4.40, 200_000),
        ("openai/o3", "o3", 2.0, 8.0, 200_000),
        ("openai/o3-mini", "o3-mini", 1.10, 4.40, 200_000),
        ("google/gemini-2.5-flash-preview:thinking", "Gemini 2.5 Flash", 0.15, 0.60, 1_048_576),
        ("google/gemini-2.5-pro-preview-03-25", "Gemini 2.5 Pro", 1.25, 10.0, 1_048_576),
        ("deepseek/deepseek-r1-0528:free", "DeepSeek R1 (free)", 0.0, 0.0, 163_840),
        ("deepseek/deepseek-r1-0528", "DeepSeek R1", 0.55, 2.19, 163_840),
        ("meta-llama/llama-4-maverick-17b-128e-instruct", "Llama 4 Maverick", 0.20, 0.20, 128_000),
        ("x-ai/grok-3-beta", "Grok 3 Beta", 3.0, 15.0, 131_072),
        ("qwen/qwq-32b", "Qwen QwQ 32B", 0.29, 0.39, 128_000),
    ],
    "google": [
        ("gemini-3-pro", "Gemini 3 Pro", 1.25, 10.0, 1_048_576),
        ("gemini-3-flash", "Gemini 3 Flash", 0.15, 0.60, 1_048_576),
        ("gemini-2.5-flash", "Gemini 2.5 Flash", 0.15, 0.60, 1_048_576),
    ],
    "github-copilot": [
        ("gpt-5", "GPT-5 (via Copilot)", 0.0, 0.0, 128_000),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5 (via Copilot)", 0.0, 0.0, 200_000),
        ("o4-mini", "o4-mini (via Copilot)", 0.0, 0.0, 128_000),
        ("gemini-2.5-pro", "Gemini 2.5 Pro (via Copilot)", 0.0, 0.0, 128_000),
    ],
    "antigravity": [
        ("claude-opus-4-6-thinking", "Claude Opus 4.6 Thinking", 0.0, 0.0, 200_000),
        ("claude-opus-4-5-thinking", "Claude Opus 4.5 Thinking", 0.0, 0.0, 200_000),
        ("claude-sonnet-4-5-thinking", "Claude Sonnet 4.5 Thinking", 0.0, 0.0, 200_000),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5", 0.0, 0.0, 200_000),
        ("gemini-2.5-flash", "Gemini 2.5 Flash", 0.0, 0.0, 1_048_576),
        ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", 0.0, 0.0, 1_048_576),
        ("gemini-2.5-flash-thinking", "Gemini 2.5 Flash Thinking", 0.0, 0.0, 1_048_576),
        ("gemini-2.5-pro", "Gemini 2.5 Pro", 0.0, 0.0, 1_048_576),
        ("gemini-3-flash", "Gemini 3 Flash", 0.0, 0.0, 1_048_576),
        ("gemini-3-pro-high", "Gemini 3 Pro High", 0.0, 0.0, 1_048_576),
        ("gemini-3-pro-image", "Gemini 3 Pro Image", 0.0, 0.0, 1_048_576),
        ("gemini-3-pro-low", "Gemini 3 Pro Low", 0.0, 0.0, 1_048_576),
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
                "openrouter": "OPENROUTER",
                "anthropic": "ANTHROPIC",
                "google": "GOOGLE",
                "github-copilot": "GITHUB COPILOT",
            }.get(provider_id, provider_id.upper())
            console.print(f"  [bold green]●[/] [bold cyan]{provider_label}[/] [dim]({auth_type} connected)[/]")
            for entry in models:
                model_id, model_name = entry[0], entry[1]
                cost_in = entry[2] if len(entry) > 2 else 0.0
                ctx = entry[4] if len(entry) > 4 else 0
                full_model = f"{provider_id}/{model_id}"
                available_options.append(full_model)
                price_str = f"${cost_in:.2f}/M" if cost_in > 0 else "$0"
                ctx_str = f"{ctx // 1000}K" if ctx > 0 else ""
                console.print(f"    [bold]{option_num}.[/] {model_name} [dim]{price_str}  {ctx_str}[/]")
                option_num += 1
            console.print()

        # Show disconnected providers (greyed out)
        if disconnected_providers:
            for provider_id, models in disconnected_providers:
                provider_label = {
                    "esprit": "ESPRIT (YOUR SUBSCRIPTION)",
                    "antigravity": "ANTIGRAVITY",
                    "openai": "OPENAI",
                    "openrouter": "OPENROUTER",
                    "anthropic": "ANTHROPIC",
                    "google": "GOOGLE",
                    "github-copilot": "GITHUB COPILOT",
                }.get(provider_id, provider_id.upper())
                console.print(f"  [dim]○ {provider_label} (not connected)[/]")
                for entry in models:
                    model_name = entry[1]
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
            for entry in models:
                if entry[0] == model:
                    model = f"{provider_id}/{entry[0]}"
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
