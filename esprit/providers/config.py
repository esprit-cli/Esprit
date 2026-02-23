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
    "opencode": [
        ("gpt-5.2-codex", "GPT-5.2 Codex (recommended)"),
        ("gpt-5.1-codex", "GPT-5.1 Codex"),
        ("gpt-5.1-codex-max", "GPT-5.1 Codex Max"),
        ("gpt-5.1-codex-mini", "GPT-5.1 Codex Mini"),
        ("gpt-5-codex", "GPT-5 Codex"),
        ("gpt-5.2", "GPT-5.2"),
        ("gpt-5.1", "GPT-5.1"),
        ("gpt-5", "GPT-5"),
        ("gpt-5-nano", "GPT-5 Nano"),
        ("claude-opus-4-6", "Claude Opus 4.6"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
        ("claude-opus-4-5", "Claude Opus 4.5"),
        ("claude-sonnet-4-5", "Claude Sonnet 4.5"),
        ("claude-opus-4-1", "Claude Opus 4.1"),
        ("claude-sonnet-4", "Claude Sonnet 4"),
        ("claude-haiku-4-5", "Claude Haiku 4.5"),
        ("claude-3-5-haiku", "Claude Haiku 3.5"),
        ("gemini-3.1-pro", "Gemini 3.1 Pro"),
        ("gemini-3-pro", "Gemini 3 Pro"),
        ("gemini-3-flash", "Gemini 3 Flash"),
        ("kimi-k2.5", "Kimi K2.5"),
        ("kimi-k2-thinking", "Kimi K2 Thinking"),
        ("kimi-k2", "Kimi K2"),
        ("qwen3-coder", "Qwen3 Coder"),
        ("glm-5", "GLM-5"),
        ("glm-4.7", "GLM-4.7"),
        ("glm-4.6", "GLM-4.6"),
        ("minimax-m2.5", "MiniMax M2.5"),
        ("minimax-m2.1", "MiniMax M2.1"),
        ("grok-code", "Grok Code Fast 1"),
        ("big-pickle", "Big Pickle"),
        ("glm-5-free", "GLM-5 Free"),
        ("glm-4.7-free", "GLM-4.7 Free"),
        ("kimi-k2.5-free", "Kimi K2.5 Free"),
        ("minimax-m2.1-free", "MiniMax M2.1 Free"),
        ("minimax-m2.5-free", "MiniMax M2.5 Free"),
        ("trinity-large-preview-free", "Trinity Large Preview Free"),
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

# Public OpenCode models available without explicit OpenCode credentials.
# Keep this list conservative: models with explicit "free" markers plus
# known no-auth models observed in OpenCode CLI.
_OPENCODE_ALWAYS_PUBLIC_MODELS: frozenset[str] = frozenset(
    {
        "gpt-5-nano",
        "big-pickle",
    }
)

_OPENCODE_PROVIDER_ALIASES: dict[str, str] = {
    "zen": "opencode",
    "codex": "openai",
}


def get_opencode_config_path() -> Path:
    """Resolve opencode.json path following XDG_CONFIG_HOME."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config:
        return Path(xdg_config) / "opencode" / "opencode.json"
    return Path.home() / ".config" / "opencode" / "opencode.json"


def _load_opencode_route_models() -> dict[str, list[tuple[str, str]]]:
    """Load provider/model routes from OpenCode config for menu parity."""
    config_path = get_opencode_config_path()
    if not config_path.exists():
        return {}

    try:
        with config_path.open(encoding="utf-8") as f:
            config = json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}

    providers = config.get("provider")
    if not isinstance(providers, dict):
        return {}

    routes: dict[str, list[tuple[str, str]]] = {}

    for raw_provider_id, provider_config in providers.items():
        if not isinstance(raw_provider_id, str) or not isinstance(provider_config, dict):
            continue
        raw_models = provider_config.get("models")
        if not isinstance(raw_models, dict):
            continue

        provider_id = _OPENCODE_PROVIDER_ALIASES.get(raw_provider_id.strip().lower(), raw_provider_id.strip().lower())

        for raw_model_id, model_config in raw_models.items():
            if not isinstance(raw_model_id, str):
                continue
            model_id = raw_model_id.strip()
            if not model_id:
                continue

            display_name = model_id
            if isinstance(model_config, dict):
                raw_name = model_config.get("name")
                if isinstance(raw_name, str) and raw_name.strip():
                    display_name = raw_name.strip()

            target_provider = provider_id
            target_model = model_id

            # OpenCode reroutes Antigravity models through a google provider section.
            # Expose these under Esprit's Antigravity provider IDs for runtime parity.
            if target_provider == "google" and target_model.startswith("antigravity-"):
                target_provider = "antigravity"
                target_model = target_model.removeprefix("antigravity-")

            if target_provider not in AVAILABLE_MODELS:
                continue

            routes.setdefault(target_provider, []).append((target_model, f"{display_name} [OpenCode route]"))

    return routes


def get_available_models() -> dict[str, list[tuple[str, str]]]:
    """Get model catalog, merged with compatible OpenCode route definitions."""
    merged: dict[str, list[tuple[str, str]]] = {
        provider_id: list(models)
        for provider_id, models in AVAILABLE_MODELS.items()
    }

    route_models = _load_opencode_route_models()
    for provider_id, models in route_models.items():
        existing_ids = {model_id for model_id, _ in merged.get(provider_id, [])}
        for model_id, model_name in models:
            if model_id in existing_ids:
                continue
            merged.setdefault(provider_id, []).append((model_id, model_name))
            existing_ids.add(model_id)

    return merged


def get_public_opencode_models(
    models_by_provider: dict[str, list[tuple[str, str]]] | None = None,
) -> set[str]:
    """Return OpenCode model IDs that can be used without provider login."""
    catalog = models_by_provider or get_available_models()
    public_models = set(_OPENCODE_ALWAYS_PUBLIC_MODELS)

    for model_id, model_name in catalog.get("opencode", []):
        if model_id.endswith("-free") or "free" in model_name.lower():
            public_models.add(model_id)

    return public_models


def has_public_opencode_models(
    models_by_provider: dict[str, list[tuple[str, str]]] | None = None,
) -> bool:
    """Check whether at least one public OpenCode model is available."""
    return bool(get_public_opencode_models(models_by_provider))


def is_public_opencode_model(
    model_name: str | None,
    models_by_provider: dict[str, list[tuple[str, str]]] | None = None,
) -> bool:
    """Check if model is an OpenCode model that supports no-auth access."""
    if not model_name:
        return False
    model_lower = model_name.strip().lower()
    if "/" not in model_lower:
        return False
    provider_id, bare_model = model_lower.split("/", 1)
    if provider_id not in {"opencode", "zen"}:
        return False
    return bare_model in get_public_opencode_models(models_by_provider)


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
    models_by_provider = get_available_models()
    public_opencode_models = get_public_opencode_models(models_by_provider)

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

        for provider_id, models in models_by_provider.items():
            if provider_id in _multi_account:
                has_creds = pool.has_accounts(provider_id)
            elif provider_id == "esprit":
                # Esprit subscription uses platform credentials, not token store
                from esprit.auth.credentials import is_authenticated
                has_creds = is_authenticated()
            elif provider_id == "opencode":
                has_creds = token_store.has_credentials(provider_id) or bool(public_opencode_models)
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
            elif provider_id == "opencode":
                creds = token_store.get(provider_id)
                auth_type = creds.type.upper() if creds else "PUBLIC"
            else:
                creds = token_store.get(provider_id)
                auth_type = creds.type.upper() if creds else "OAUTH"
            provider_label = {
                "esprit": "ESPRIT (YOUR SUBSCRIPTION)",
                "antigravity": "ANTIGRAVITY",
                "openai": "OPENAI",
                "anthropic": "ANTHROPIC",
                "google": "GOOGLE",
                "opencode": "OPENCODE ZEN",
                "github-copilot": "GITHUB COPILOT",
            }.get(provider_id, provider_id.upper())
            connection_hint = f"{auth_type} connected" if auth_type != "PUBLIC" else "PUBLIC no-auth"
            console.print(f"  [bold green]●[/] [bold cyan]{provider_label}[/] [dim]({connection_hint})[/]")
            models_to_show = models
            if provider_id == "opencode" and not token_store.has_credentials("opencode"):
                models_to_show = [
                    (model_id, model_name)
                    for model_id, model_name in models
                    if model_id in public_opencode_models
                ]
            for model_id, model_name in models_to_show:
                full_model = f"{provider_id}/{model_id}"
                available_options.append(full_model)
                console.print(f"    [bold]{option_num}.[/] {model_name} [dim]({model_id})[/]")
                option_num += 1
            if models_to_show:
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
                    "opencode": "OPENCODE ZEN",
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
        for provider_id, models in models_by_provider.items():
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
