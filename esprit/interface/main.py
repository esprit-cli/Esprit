#!/usr/bin/env python3
"""
Esprit Agent Interface

Commands:
  esprit scan <target>       Run a penetration test scan
  esprit provider login      Login/connect an LLM provider
  esprit provider status     Show provider authentication status
  esprit provider logout     Logout from a provider
"""

import argparse
import asyncio
import logging
import os
import platform
import shutil
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any

import litellm
from docker.errors import DockerException, ImageNotFound
from rich.console import Console
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.text import Text

from esprit.config import Config, apply_saved_config, save_current_config


apply_saved_config()

from esprit.interface.cli import run_cli  # noqa: E402
from esprit.interface.launchpad import LaunchpadResult, run_launchpad  # noqa: E402
from esprit.interface.tui import run_tui  # noqa: E402
from esprit.interface.updater import apply_update, has_pending_update  # noqa: E402
from esprit.interface.utils import (  # noqa: E402
    assign_workspace_subdirs,
    build_final_stats_text,
    build_subscription_quota_lines,
    check_docker_connection,
    clone_repository,
    collect_local_sources,
    generate_run_name,
    image_exists,
    infer_target_type,
    rewrite_localhost_targets,
    validate_config_file,
    validate_llm_response,
)
from esprit.runtime.docker_runtime import HOST_GATEWAY_HOSTNAME  # noqa: E402
from esprit.telemetry import posthog  # noqa: E402
from esprit.telemetry.tracer import get_global_tracer  # noqa: E402


logging.getLogger().setLevel(logging.ERROR)

_PAID_SUBSCRIPTION_PLANS = {"pro", "team", "enterprise"}


def _is_paid_subscription_plan(plan: str | None) -> bool:
    return (plan or "").strip().lower() in _PAID_SUBSCRIPTION_PLANS


def _is_cloud_subscription_model(model_name: str | None) -> bool:
    if not model_name:
        return True
    model_lower = model_name.strip().lower()
    return model_lower.startswith("esprit/") or model_lower.startswith("bedrock/")


def _should_use_cloud_runtime() -> bool:
    """Check if scan runtime should be routed to Esprit Cloud."""
    from esprit.auth.credentials import get_user_plan, is_authenticated, verify_subscription

    if not is_authenticated():
        return False

    if not _is_paid_subscription_plan(get_user_plan()):
        return False

    model_name = Config.get("esprit_llm")
    if not _is_cloud_subscription_model(model_name):
        return False

    verification = verify_subscription()
    if verification.get("valid", False):
        cloud_enabled = verification.get("cloud_enabled")
        return bool(cloud_enabled) if cloud_enabled is not None else True

    # If subscription verification endpoint is temporarily unreachable,
    # trust local credentials and continue with cloud mode.
    error = str(verification.get("error", ""))
    return error.startswith("Subscription verification failed:")


def validate_environment() -> None:  # noqa: PLR0912, PLR0915
    from esprit.llm.config import DEFAULT_MODEL

    console = Console()
    missing_required_vars = []
    missing_optional_vars = []

    # ESPRIT_LLM is no longer required since we have a default model
    # if not Config.get("esprit_llm"):
    #     missing_required_vars.append("ESPRIT_LLM")

    has_base_url = any(
        [
            Config.get("llm_api_base"),
            Config.get("openai_api_base"),
            Config.get("litellm_base_url"),
            Config.get("ollama_api_base"),
        ]
    )

    if not Config.get("llm_api_key"):
        missing_optional_vars.append("LLM_API_KEY")

    if not has_base_url:
        missing_optional_vars.append("LLM_API_BASE")

    if not Config.get("perplexity_api_key"):
        missing_optional_vars.append("PERPLEXITY_API_KEY")

    if not Config.get("esprit_reasoning_effort"):
        missing_optional_vars.append("ESPRIT_REASONING_EFFORT")

    if missing_required_vars:
        error_text = Text()
        error_text.append("MISSING REQUIRED ENVIRONMENT VARIABLES", style="bold red")
        error_text.append("\n\n", style="white")

        for var in missing_required_vars:
            error_text.append(f"• {var}", style="bold yellow")
            error_text.append(" is not set\n", style="white")

        if missing_optional_vars:
            error_text.append("\nOptional environment variables:\n", style="dim white")
            for var in missing_optional_vars:
                error_text.append(f"• {var}", style="dim yellow")
                error_text.append(" is not set\n", style="dim white")

        error_text.append("\nRequired environment variables:\n", style="white")
        for var in missing_required_vars:
            if var == "ESPRIT_LLM":
                error_text.append("• ", style="white")
                error_text.append("ESPRIT_LLM", style="bold cyan")
                error_text.append(
                    " - Model name to use with litellm (e.g., 'openai/gpt-5')\n",
                    style="white",
                )

        if missing_optional_vars:
            error_text.append("\nOptional environment variables:\n", style="white")
            for var in missing_optional_vars:
                if var == "LLM_API_KEY":
                    error_text.append("• ", style="white")
                    error_text.append("LLM_API_KEY", style="bold cyan")
                    error_text.append(
                        " - API key for the LLM provider "
                        "(not needed for local models, Vertex AI, AWS, etc.)\n",
                        style="white",
                    )
                elif var == "LLM_API_BASE":
                    error_text.append("• ", style="white")
                    error_text.append("LLM_API_BASE", style="bold cyan")
                    error_text.append(
                        " - Custom API base URL if using local models (e.g., Ollama, LMStudio)\n",
                        style="white",
                    )
                elif var == "PERPLEXITY_API_KEY":
                    error_text.append("• ", style="white")
                    error_text.append("PERPLEXITY_API_KEY", style="bold cyan")
                    error_text.append(
                        " - API key for Perplexity AI web search (enables real-time research)\n",
                        style="white",
                    )
                elif var == "ESPRIT_REASONING_EFFORT":
                    error_text.append("• ", style="white")
                    error_text.append("ESPRIT_REASONING_EFFORT", style="bold cyan")
                    error_text.append(
                        " - Reasoning effort level: none, minimal, low, medium, high, xhigh "
                        "(default: high)\n",
                        style="white",
                    )

        error_text.append("\nExample setup:\n", style="white")
        error_text.append("export ESPRIT_LLM='openai/gpt-5'\n", style="dim white")

        if missing_optional_vars:
            for var in missing_optional_vars:
                if var == "LLM_API_KEY":
                    error_text.append(
                        "export LLM_API_KEY='your-api-key-here'  "
                        "# not needed for local models, Vertex AI, AWS, etc.\n",
                        style="dim white",
                    )
                elif var == "LLM_API_BASE":
                    error_text.append(
                        "export LLM_API_BASE='http://localhost:11434'  "
                        "# needed for local models only\n",
                        style="dim white",
                    )
                elif var == "PERPLEXITY_API_KEY":
                    error_text.append(
                        "export PERPLEXITY_API_KEY='your-perplexity-key-here'\n", style="dim white"
                    )
                elif var == "ESPRIT_REASONING_EFFORT":
                    error_text.append(
                        "export ESPRIT_REASONING_EFFORT='high'\n",
                        style="dim white",
                    )

        panel = Panel(
            error_text,
            title="[bold white]ESPRIT",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )

        console.print("\n")
        console.print(panel)
        console.print()
        sys.exit(1)


def _docker_health_check(console: Console, config: type) -> bool:
    """Consolidated pre-scan Docker health check.

    Returns True if all checks pass, False otherwise.
    """
    import docker as docker_lib

    failures: list[tuple[str, str]] = []

    # 1. Docker daemon running
    try:
        client = docker_lib.from_env()
        client.ping()
    except Exception:
        failures.append(
            ("Docker daemon is not running", "Start Docker Desktop or run: sudo systemctl start docker"),
        )

    # 2. Sandbox image configured (actual pull happens in next startup step)
    if not failures:
        image_name = str(config.get("esprit_image") or "").strip()
        if not image_name:
            failures.append(
                (
                    "Sandbox image is not configured",
                    "Set ESPRIT_IMAGE to a valid image (for example: improdead/esprit-sandbox:latest)",
                ),
            )
        elif not image_exists(client, image_name):
            console.print(
                f"[dim]Sandbox image '{image_name}' is not present locally yet; "
                "it will be pulled in the next step.[/]",
            )

    # 3. Sufficient disk space (≥ 2 GB on Docker data-root)
    if not failures:
        try:
            info = client.info()
            docker_root = info.get("DockerRootDir", "/var/lib/docker")
            usage = shutil.disk_usage(docker_root)
            min_free = 2 * 1024 * 1024 * 1024  # 2 GB
            if usage.free < min_free:
                free_gb = usage.free / (1024 ** 3)
                failures.append(
                    (
                        f"Low disk space on Docker data-root ({free_gb:.1f} GB free, need ≥ 2 GB)",
                        f"Free up disk space in {docker_root}",
                    ),
                )
        except OSError:
            pass  # non-fatal: skip check if path is inaccessible

    if failures:
        error_text = Text()
        error_text.append("DOCKER HEALTH CHECK FAILED", style="bold red")
        error_text.append("\n", style="white")
        for problem, fix in failures:
            error_text.append(f"\n✗ {problem}\n", style="red")
            error_text.append(f"  ➜ {fix}\n", style="dim white")

        console.print(
            "\n",
            Panel(
                error_text,
                title="[bold white]ESPRIT",
                title_align="left",
                border_style="red",
                padding=(1, 2),
            ),
            "\n",
        )
        return False

    return True


def check_docker_installed() -> None:
    if shutil.which("docker") is None:
        console = Console()
        error_text = Text()
        error_text.append("DOCKER NOT INSTALLED", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append("The 'docker' CLI was not found in your PATH.\n", style="white")
        error_text.append(
            "Please install Docker and ensure the 'docker' command is available.\n\n", style="white"
        )
        error_text.append("Install: https://docs.docker.com/get-docker/\n", style="dim white")

        panel = Panel(
            error_text,
            title="[bold white]ESPRIT",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )
        console.print("\n", panel, "\n")
        sys.exit(1)


def ensure_docker_running() -> None:
    """Check if Docker daemon is running; auto-start on macOS if possible."""
    import subprocess
    import time

    console = Console()

    try:
        import docker as docker_lib
        docker_lib.from_env()
        return  # Docker is running
    except Exception:
        pass

    # Try to auto-start Docker on macOS
    if sys.platform == "darwin":
        console.print()
        console.print("[dim]Docker daemon not running. Starting Docker Desktop...[/]")

        try:
            subprocess.Popen(
                ["open", "-a", "Docker"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            console.print("[red]Could not start Docker Desktop.[/]")
            console.print("[dim]Please start Docker Desktop manually and try again.[/]")
            console.print()
            sys.exit(1)

        # Wait for Docker to become available
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]Waiting for Docker to start..."),
            BarColumn(),
            TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("docker", total=60)
            for i in range(60):
                time.sleep(1)
                progress.update(task, advance=1)
                try:
                    import docker as docker_lib
                    docker_lib.from_env()
                    progress.update(task, completed=60)
                    console.print("[green]✓ Docker started.[/]")
                    return
                except Exception:  # noqa: BLE001
                    pass

        console.print("[red]Docker did not start in time.[/]")
        console.print("[dim]Please start Docker Desktop manually and try again.[/]")
        console.print()
        sys.exit(1)
    else:
        console.print()
        console.print("[yellow]Docker daemon is not running.[/]")
        console.print("[dim]Please start Docker and try again.[/]")
        console.print()
        sys.exit(1)


def ensure_provider_configured() -> bool:
    """Check if at least one LLM provider is configured. Return True if ready."""
    from esprit.providers.token_store import TokenStore
    from esprit.providers.account_pool import get_account_pool
    from esprit.providers.config import has_public_opencode_models, is_public_opencode_model

    # Check for direct API key
    if Config.get("llm_api_key"):
        return True

    # Check for Esprit subscription (platform credentials)
    try:
        from esprit.auth.credentials import is_authenticated
        if is_authenticated():
            return True
    except ImportError:
        pass

    # OpenCode exposes select no-auth models; allow those without login.
    if is_public_opencode_model(Config.get("esprit_llm")):
        return True

    # Check for OAuth providers (single-credential)
    token_store = TokenStore()
    for provider_id in ["anthropic", "google", "github-copilot", "opencode"]:
        if token_store.has_credentials(provider_id):
            return True

    # Check for multi-account providers
    pool = get_account_pool()
    for provider_id in ["openai", "antigravity"]:  # noqa: from constants.MULTI_ACCOUNT_PROVIDERS
        if pool.has_accounts(provider_id):
            return True
        # Also check token_store as fallback (TUI may have saved there)
        if token_store.has_credentials(provider_id):
            return True

    if has_public_opencode_models():
        return True

    return False


def _get_configured_providers() -> list[tuple[str, str]]:
    """Return list of (provider_id, detail) for all configured providers."""
    from esprit.providers.token_store import TokenStore
    from esprit.providers.account_pool import get_account_pool
    from esprit.providers.config import has_public_opencode_models

    token_store = TokenStore()
    pool = get_account_pool()
    result = []

    from esprit.providers.constants import MULTI_ACCOUNT_PROVIDERS as _multi_account

    # Check Esprit subscription (platform credentials)
    try:
        from esprit.auth.credentials import is_authenticated as _is_esprit_auth, get_credentials as _get_esprit_creds
        if _is_esprit_auth():
            _ecreds = _get_esprit_creds()
            _plan = _ecreds.get("plan", "free").upper() if _ecreds else "FREE"
            _email = _ecreds.get("email", "") if _ecreds else ""
            detail = _email if _email else f"Platform ({_plan})"
            result.append(("esprit", detail))
    except ImportError:
        pass

    for provider_id in ["antigravity", "opencode", "openai", "anthropic", "google", "github-copilot"]:
        if provider_id in _multi_account:
            if pool.has_accounts(provider_id):
                count = pool.account_count(provider_id)
                acct = pool.get_best_account(provider_id)
                email = acct.email if acct else "unknown"
                detail = f"{email}" + (f" (+{count - 1} more)" if count > 1 else "")
                result.append((provider_id, detail))
            elif token_store.has_credentials(provider_id):
                result.append((provider_id, "API key"))
        else:
            if provider_id == "opencode":
                if token_store.has_credentials(provider_id):
                    creds = token_store.get(provider_id)
                    detail = creds.type.upper() if creds else "configured"
                    result.append((provider_id, detail))
                elif has_public_opencode_models():
                    result.append((provider_id, "Public models (no auth)"))
            elif token_store.has_credentials(provider_id):
                creds = token_store.get(provider_id)
                detail = creds.type.upper() if creds else "configured"
                result.append((provider_id, detail))

    # Direct API key (provider-agnostic)
    if Config.get("llm_api_key"):
        result.append(("direct", "LLM_API_KEY env"))

    return result


def _get_available_models(configured_providers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return list of (model_id, display_name) available from configured providers."""
    from esprit.providers.config import get_available_models, get_public_opencode_models
    from esprit.providers.token_store import TokenStore

    catalog = get_available_models()
    provider_ids = {p[0] for p in configured_providers}
    models = []
    token_store = TokenStore()
    public_opencode_models = get_public_opencode_models(catalog)

    for provider_id, model_list in catalog.items():
        if provider_id in provider_ids:
            models_to_show = model_list
            if provider_id == "opencode" and not token_store.has_credentials("opencode"):
                models_to_show = [
                    (model_id, display_name)
                    for model_id, display_name in model_list
                    if model_id in public_opencode_models
                ]
            for model_id, display_name in models_to_show:
                full_id = f"{provider_id}/{model_id}"
                models.append((full_id, f"{display_name} [{provider_id}]"))

    # If direct API key is configured, any model might work
    if "direct" in provider_ids:
        current = Config.get("esprit_llm")
        if current and not any(m[0] == current for m in models):
            models.append((current, f"{current} [direct API key]"))

    return models


def pre_scan_setup(non_interactive: bool = False) -> bool:
    """Interactive pre-scan checks. Returns True if ready to scan, False to abort."""
    from rich.prompt import Prompt, Confirm
    from rich.table import Table
    from esprit.llm.config import DEFAULT_MODEL

    console = Console()
    console.print()

    # --- Step 1: Check providers ---
    providers = _get_configured_providers()
    if not providers:
        console.print("[bold red]No LLM provider configured.[/]")
        console.print()
        console.print("Set up a provider first:")
        console.print("  [cyan]esprit provider login[/]          # Connect provider (OAuth/API)")
        console.print("  [cyan]esprit provider api-key[/]        # Direct API key")
        console.print()
        return False

    # Show configured providers
    console.print("[bold]Pre-scan checks[/]")
    console.print()

    table = Table(show_header=True, header_style="bold", show_lines=False, pad_edge=False)
    table.add_column("Provider", style="cyan")
    table.add_column("Type", style="dim")
    table.add_column("Account", style="white")
    for pid, detail in providers:
        display_name = {
            "esprit": "[bold #6366f1]Esprit[/] [dim](Subscription)[/]",
            "antigravity": "[bold #a78bfa]Antigravity[/] [dim](Free)[/]",
            "opencode": "[bold #10b981]OpenCode Zen[/]",
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "google": "Google",
            "github-copilot": "GitHub Copilot",
            "direct": "Direct",
        }.get(pid, pid)
        auth_type = {
            "esprit": "[green]Platform[/]",
            "antigravity": "[green]OAuth[/]",
            "opencode": "[green]Public[/]" if detail.lower().startswith("public") else "[yellow]API Key[/]",
            "openai": "[green]OAuth[/]" if "@" in detail else "[yellow]API Key[/]",
            "anthropic": "[yellow]API Key[/]",
            "google": "[green]OAuth[/]",
            "github-copilot": "[green]OAuth[/]",
            "direct": "[yellow]Env Var[/]",
        }.get(pid, "")
        table.add_row(display_name, auth_type, detail)
    console.print(table)
    console.print()

    # --- Step 2: Check/select model ---
    current_model = Config.get("esprit_llm")
    available_models = _get_available_models(providers)

    if current_model:
        bare = current_model.split("/", 1)[-1] if "/" in current_model else current_model
        provider_prefix = current_model.split("/", 1)[0] if "/" in current_model else ""
        provider_badge = {
            "esprit": "[bold #6366f1]ES[/]",
            "antigravity": "[bold #a78bfa]AG[/]",
            "openai": "[bold #74aa9c]OAI[/]",
            "anthropic": "[bold #d4a27f]CC[/]",
            "google": "[bold #4285f4]GG[/]",
            "github-copilot": "[bold white]CO[/]",
            "opencode": "[bold #10b981]OZ[/]",
        }.get(provider_prefix, "")
        if provider_badge:
            console.print(f"[bold]Model:[/] {provider_badge} {bare}")
        else:
            console.print(f"[bold]Model:[/] {current_model}")
    elif available_models:
        console.print("[yellow]No model selected.[/]")
    else:
        console.print("[yellow]No model selected and no models available from configured providers.[/]")
        console.print("[dim]Set ESPRIT_LLM environment variable or run 'esprit config model'[/]")
        console.print()
        return False

    if not current_model and available_models:
        if non_interactive:
            # Auto-select the first available model in non-interactive mode
            selected_model = available_models[0][0]
            os.environ["ESPRIT_LLM"] = selected_model
            Config.save_current()
            current_model = selected_model
            console.print(f"[dim]Auto-selected model: {current_model}[/]")
        else:
            console.print()
            console.print("[bold]Select a model:[/]")
            for i, (model_id, display) in enumerate(available_models, 1):
                console.print(f"  {i}. {display} [dim]({model_id})[/]")
            console.print()
            choice = Prompt.ask(
                "Enter number",
                choices=[str(i) for i in range(1, len(available_models) + 1)],
            )
            selected_model = available_models[int(choice) - 1][0]
            os.environ["ESPRIT_LLM"] = selected_model
            Config.save_current()
            current_model = selected_model
            console.print(f"[green]Model set to: {current_model}[/]")

    # --- Step 3: Show active account for multi-account providers ---
    from esprit.providers.account_pool import get_account_pool
    from esprit.providers.antigravity import ANTIGRAVITY_MODELS

    pool = get_account_pool()
    model_lower = (current_model or "").lower()
    bare_model = model_lower.split("/", 1)[-1] if "/" in model_lower else model_lower

    # Determine which provider this model routes through
    routing = None
    if model_lower.startswith("antigravity/") or (
        bare_model in ANTIGRAVITY_MODELS and pool.has_accounts("antigravity")
    ):
        routing = "antigravity"
    elif model_lower.startswith("openai/") and pool.has_accounts("openai"):
        routing = "openai"

    if routing:
        acct = pool.get_best_account(routing)
        if acct:
            count = pool.account_count(routing)
            console.print(
                f"[bold]Account:[/] {acct.email}"
                + (f" [dim](+{count - 1} available for rotation)[/]" if count > 1 else "")
            )

    console.print()

    # --- Step 4: Confirm ---
    if not non_interactive:
        if not Confirm.ask("[bold]Proceed with scan?[/]", default=True):
            console.print("[dim]Scan cancelled.[/]")
            return False

    console.print()
    return True


async def warm_up_llm() -> None:
    from esprit.llm.config import DEFAULT_MODEL
    from esprit.llm.model_routing import to_litellm_model_name
    from esprit.llm.api_base import (
        configured_api_base,
        detect_conflicting_provider_base_env,
        resolve_api_base,
    )
    from esprit.providers.litellm_integration import (
        get_provider_api_key,
        get_provider_api_base,
        get_provider_headers,
        should_use_oauth,
    )

    console = Console()
    model_name = ""

    try:
        model_name = Config.get("esprit_llm") or DEFAULT_MODEL

        # Codex OAuth models use a non-standard API — skip warm-up test
        model_lower = model_name.lower() if model_name else ""
        is_codex_oauth = "codex" in model_lower
        if is_codex_oauth and should_use_oauth(model_name):
            console.print("[dim]Codex OAuth configured — skipping warm-up test[/]")
            return

        # Esprit subscription routes through proxy — skip warm-up test
        if model_lower.startswith("esprit/"):
            console.print("[dim]Esprit subscription configured — skipping warm-up test[/]")
            return

        # Antigravity models bypass litellm entirely — skip warm-up test
        if model_lower.startswith("antigravity/"):
            console.print("[dim]Antigravity configured — skipping warm-up test[/]")
            return

        # Also skip for google/ or bare models that route through Antigravity
        from esprit.providers.antigravity import ANTIGRAVITY_MODELS
        from esprit.providers.account_pool import get_account_pool

        bare_model = model_lower.split("/", 1)[-1] if "/" in model_lower else model_lower
        if bare_model in ANTIGRAVITY_MODELS and get_account_pool().has_accounts("antigravity"):
            console.print("[dim]Antigravity configured — skipping warm-up test[/]")
            return

        test_messages = [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Reply with just 'OK'."},
        ]
        llm_timeout = int(Config.get("llm_timeout") or "300")

        def _build_completion_kwargs(candidate_model: str) -> dict[str, Any]:
            api_key = Config.get("llm_api_key")
            if not api_key:
                oauth_key = get_provider_api_key(candidate_model)
                if oauth_key:
                    api_key = oauth_key

            routed_model = to_litellm_model_name(candidate_model) or candidate_model
            completion_kwargs: dict[str, Any] = {
                "model": routed_model,
                "messages": test_messages,
                "timeout": llm_timeout,
            }

            if api_key:
                completion_kwargs["api_key"] = api_key

            api_base = resolve_api_base(candidate_model)
            if not api_base:
                api_base = get_provider_api_base(candidate_model)
            if api_base:
                completion_kwargs["api_base"] = api_base

            extra_headers = get_provider_headers(candidate_model)
            if extra_headers:
                completion_kwargs["extra_headers"] = {
                    **completion_kwargs.get("extra_headers", {}),
                    **extra_headers,
                }

            return completion_kwargs

        def _warm_up_once(candidate_model: str) -> None:
            response = litellm.completion(**_build_completion_kwargs(candidate_model))
            validate_llm_response(response)

        try:
            _warm_up_once(model_name)
            return
        except Exception as primary_error:
            primary_error_text = str(primary_error)
            if model_lower.startswith(("opencode/", "zen/")):
                from esprit.providers.config import get_available_models, get_public_opencode_models

                preferred_free_models = [
                    "minimax-m2.5-free",
                    "kimi-k2.5-free",
                    "gpt-5-nano",
                    "minimax-m2.1-free",
                    "trinity-large-preview-free",
                ]
                catalog = get_available_models()
                public_models = get_public_opencode_models(catalog)
                bare_model = model_name.split("/", 1)[1] if "/" in model_name else model_name

                fallback_bare_models = [
                    model_id for model_id in preferred_free_models
                    if model_id in public_models and model_id != bare_model
                ]
                fallback_bare_models.extend(
                    sorted(
                        model_id
                        for model_id in public_models
                        if model_id not in fallback_bare_models and model_id != bare_model
                    )
                )

                for fallback_bare in fallback_bare_models:
                    fallback_model = f"opencode/{fallback_bare}"
                    try:
                        _warm_up_once(fallback_model)
                    except Exception:
                        continue

                    os.environ["ESPRIT_LLM"] = fallback_model
                    console.print(
                        f"[yellow]OpenCode model unavailable:[/] [dim]{model_name}[/] "
                        f"[yellow]→ switched to[/] [bold]{fallback_model}[/]"
                    )
                    return

            raise Exception(primary_error_text) from primary_error

    except Exception as e:  # noqa: BLE001
        error_text = Text()
        error_text.append("LLM CONNECTION FAILED", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append("Could not establish connection to the language model.\n", style="white")
        error_text.append("Please check your configuration and try again.\n", style="white")
        if not configured_api_base():
            conflict = detect_conflicting_provider_base_env(model_name)
            if conflict:
                env_name, env_value = conflict
                error_text.append(
                    f"\nDetected {env_name}={env_value}\n",
                    style="yellow",
                )
                error_text.append(
                    "This environment variable can override provider API routing.\n",
                    style="yellow",
                )
                error_text.append(
                    f"Unset it in your shell: unset {env_name}\n",
                    style="dim white",
                )
        error_text.append(f"\nError: {e}", style="dim white")

        error_message = str(e)
        model_lower = model_name.lower() if model_name else ""
        if (
            (model_lower.startswith("opencode/") or model_lower.startswith("zen/"))
            and "prompt_tokens" in error_message
        ):
            error_text.append(
                "\n\nOpenCode returned an upstream model error for this free model.",
                style="yellow",
            )
            error_text.append(
                "\nTry one of these public models:",
                style="yellow",
            )
            error_text.append(
                "\n  - opencode/kimi-k2.5-free"
                "\n  - opencode/minimax-m2.5-free"
                "\n  - opencode/gpt-5-nano",
                style="dim white",
            )

        panel = Panel(
            error_text,
            title="[bold white]ESPRIT",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )

        console.print("\n")
        console.print(panel)
        console.print()
        sys.exit(1)


def get_version() -> str:
    try:
        from importlib.metadata import version

        try:
            return version("esprit-cli")
        except Exception:  # noqa: BLE001
            return version("esprit-agent")
    except Exception:  # noqa: BLE001
        try:
            from esprit._version import __version__

            return __version__
        except Exception:  # noqa: BLE001
            return "unknown"


def cmd_uninstall() -> int:
    """Uninstall Esprit CLI from this machine."""
    import shutil

    console = Console()
    install_dir = Path.home() / ".esprit"
    bin_path = install_dir / "bin" / "esprit"

    console.print()
    console.print("[bold]Uninstalling Esprit CLI[/]")
    console.print()

    # Show what will be removed
    items = []
    if bin_path.exists():
        items.append(f"  Binary: {bin_path}")
    if install_dir.exists():
        items.append(f"  Config: {install_dir}")

    if not items:
        console.print("[yellow]Esprit does not appear to be installed.[/]")
        return 0

    for item in items:
        console.print(f"[dim]{item}[/]")
    console.print()

    confirm = input("Remove Esprit and all configuration? [y/N] ").strip().lower()
    if confirm != "y":
        console.print("[dim]Cancelled.[/]")
        return 0

    # Remove binary
    if bin_path.exists():
        bin_path.unlink()
        console.print("[green]✓[/] Removed binary")

    # Remove bin directory if empty
    bin_dir = install_dir / "bin"
    if bin_dir.exists() and not any(bin_dir.iterdir()):
        bin_dir.rmdir()

    # Remove config directory
    if install_dir.exists():
        shutil.rmtree(install_dir)
        console.print("[green]✓[/] Removed configuration")

    # Clean PATH from shell configs
    cleaned_shells = []
    for rc_file in [Path.home() / ".zshrc", Path.home() / ".bashrc", Path.home() / ".bash_profile"]:
        if rc_file.exists():
            content = rc_file.read_text()
            new_content = "\n".join(
                line for line in content.splitlines()
                if ".esprit/bin" not in line and line.strip() != "# esprit"
            ) + "\n"
            if new_content != content:
                rc_file.write_text(new_content)
                cleaned_shells.append(rc_file.name)

    if cleaned_shells:
        console.print(f"[green]✓[/] Cleaned PATH from {', '.join(cleaned_shells)}")

    console.print()
    console.print("[green]Esprit has been uninstalled.[/]")
    console.print("[dim]Restart your shell to update PATH.[/]")
    console.print()
    return 0


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Esprit - AI-Powered Penetration Testing",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  esprit scan <target>       Run a penetration test
  esprit provider login      Login/connect an LLM provider
  esprit provider status     Show provider authentication status

Examples:
  # Run a scan
  esprit scan https://example.com
  esprit scan github.com/user/repo
  esprit scan ./my-project

  # Provider authentication
  esprit provider login              # Interactive provider selection
  esprit provider login esprit       # Login with Esprit subscription
  esprit provider login opencode     # Connect OpenCode Zen
  esprit provider login openai       # Login to OpenAI Codex
  esprit provider login github-copilot
  esprit provider login google       # Login to Google Gemini
  esprit provider status             # Check auth status

  # Legacy mode (--target still works)
  esprit --target https://example.com
        """,
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # Provider subcommand
    provider_parser = subparsers.add_parser(
        "provider",
        help="Manage LLM provider authentication",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Supported providers:
  esprit          Esprit Subscription (Esprit Default)
  opencode        OpenCode Zen (API key)
  anthropic       Claude Pro/Max (OAuth) or API key
  openai          ChatGPT Plus/Pro / Codex (OAuth) or API key
  github-copilot  GitHub Copilot (OAuth)
  google          Google Gemini (OAuth) or API key
        """,
    )
    provider_subparsers = provider_parser.add_subparsers(dest="provider_command")
    
    provider_login = provider_subparsers.add_parser("login", help="Login/connect to a provider")
    provider_login.add_argument(
        "provider_id",
        nargs="?",
        help="Provider ID (esprit, opencode, anthropic, openai, github-copilot, google)",
    )
    
    provider_logout = provider_subparsers.add_parser("logout", help="Logout from a provider")
    provider_logout.add_argument("provider_id", nargs="?", help="Provider ID to logout from")
    
    provider_subparsers.add_parser("status", help="Show provider authentication status")
    
    provider_apikey = provider_subparsers.add_parser("api-key", help="Set API key for a provider")
    provider_apikey.add_argument("provider_id", nargs="?", help="Provider ID")
    
    # Scan subcommand
    scan_parser = subparsers.add_parser(
        "scan",
        help="Run a penetration test scan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scan_parser.add_argument(
        "target",
        nargs="+",
        help="Target(s) to test (URL, repository, local directory)",
    )
    scan_parser.add_argument("--instruction", type=str, help="Custom instructions")
    scan_parser.add_argument("--instruction-file", type=str, help="Path to instruction file")
    scan_parser.add_argument("-n", "--non-interactive", action="store_true", help="Non-interactive mode")
    scan_parser.add_argument("-m", "--scan-mode", choices=["quick", "standard", "deep"], default="deep")
    scan_parser.add_argument("--config", type=str, help="Path to custom config file")

    # Uninstall subcommand
    subparsers.add_parser(
        "uninstall",
        help="Uninstall Esprit CLI from this machine",
    )
    parser.add_argument(
        "-t",
        "--target",
        type=str,
        action="append",
        help="(Legacy) Target to test. Use 'esprit scan <target>' instead.",
    )
    parser.add_argument("--instruction", type=str, help="Custom instructions")
    parser.add_argument("--instruction-file", type=str, help="Path to instruction file")
    parser.add_argument("-n", "--non-interactive", action="store_true", help="Non-interactive mode")
    parser.add_argument("-m", "--scan-mode", choices=["quick", "standard", "deep"], default="deep")
    parser.add_argument("--config", type=str, help="Path to custom config file")
    
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"esprit {get_version()}",
    )
    parser.add_argument("--self-update", action="store_true", help="Update Esprit to the latest version")

    args = parser.parse_args()
    args.skip_pre_scan_checks = False
    
    # Handle provider subcommand
    if args.command == "provider":
        from esprit.providers.commands import (
            cmd_provider_login,
            cmd_provider_logout,
            cmd_provider_status,
            cmd_provider_set_api_key,
        )
        
        if args.provider_command == "login":
            sys.exit(cmd_provider_login(getattr(args, "provider_id", None)))
        elif args.provider_command == "logout":
            sys.exit(cmd_provider_logout(getattr(args, "provider_id", None)))
        elif args.provider_command == "status":
            sys.exit(cmd_provider_status())
        elif args.provider_command == "api-key":
            sys.exit(cmd_provider_set_api_key(getattr(args, "provider_id", None)))
        else:
            parser.parse_args(["provider", "--help"])
            sys.exit(0)
    
    # Handle uninstall subcommand
    if args.command == "uninstall":
        sys.exit(cmd_uninstall())

    # Handle scan subcommand or legacy --target
    targets = []
    if args.command == "scan":
        targets = args.target
    elif args.target:
        targets = args.target
    else:
        args.command = "launchpad"
        args.targets_info = []
        return args
    
    if hasattr(args, "instruction") and hasattr(args, "instruction_file"):
        if args.instruction and args.instruction_file:
            parser.error("Cannot specify both --instruction and --instruction-file.")

    if hasattr(args, "instruction_file") and args.instruction_file:
        instruction_path = Path(args.instruction_file)
        try:
            with instruction_path.open(encoding="utf-8") as f:
                args.instruction = f.read().strip()
        except Exception as e:
            parser.error(f"Failed to read instruction file: {e}")

    args.targets_info = _build_targets_info(targets, parser)

    return args


def _build_targets_info(
    targets: list[str], parser: argparse.ArgumentParser | None = None
) -> list[dict[str, Any]]:
    targets_info: list[dict[str, Any]] = []

    for target in targets:
        try:
            target_type, target_dict = infer_target_type(target)

            if target_type == "local_code":
                display_target = target_dict.get("target_path", target)
            else:
                display_target = target

            targets_info.append(
                {"type": target_type, "details": target_dict, "original": display_target}
            )
        except ValueError:
            if parser is not None:
                parser.error(f"Invalid target '{target}'")
            raise

    assign_workspace_subdirs(targets_info)
    rewrite_localhost_targets(targets_info, HOST_GATEWAY_HOSTNAME)
    return targets_info


def _apply_launchpad_result(args: argparse.Namespace, launchpad_result: LaunchpadResult) -> bool:
    if launchpad_result.action != "scan" or not launchpad_result.target:
        return False

    args.command = "scan"
    args.non_interactive = False
    args.scan_mode = launchpad_result.scan_mode
    args.instruction = None
    args.skip_pre_scan_checks = launchpad_result.prechecked
    args.targets_info = _build_targets_info([launchpad_result.target])
    return True


def display_completion_message(args: argparse.Namespace, results_path: Path) -> None:
    console = Console()
    tracer = get_global_tracer()

    scan_completed = False
    if tracer and tracer.scan_results:
        scan_completed = tracer.scan_results.get("scan_completed", False)

    has_vulnerabilities = tracer and len(tracer.vulnerability_reports) > 0

    completion_text = Text()
    if scan_completed:
        completion_text.append("Penetration test completed", style="bold #22c55e")
    else:
        completion_text.append("SESSION ENDED", style="bold #eab308")

    target_text = Text()
    target_text.append("Target", style="dim")
    target_text.append("  ")
    if len(args.targets_info) == 1:
        target_text.append(args.targets_info[0]["original"], style="bold white")
    else:
        target_text.append(f"{len(args.targets_info)} targets", style="bold white")
        for target_info in args.targets_info:
            target_text.append("\n        ")
            target_text.append(target_info["original"], style="white")

    stats_text = build_final_stats_text(tracer)

    panel_parts = [completion_text, "\n\n", target_text]

    if stats_text.plain:
        panel_parts.extend(["\n", stats_text])

    if scan_completed or has_vulnerabilities:
        results_text = Text()
        results_text.append("\n")
        results_text.append("Output", style="dim")
        results_text.append("  ")
        results_text.append(str(results_path), style="#60a5fa")
        panel_parts.extend(["\n", results_text])

    panel_content = Text.assemble(*panel_parts)

    border_style = "#22c55e" if scan_completed else "#eab308"

    panel = Panel(
        panel_content,
        title="[bold white]ESPRIT",
        title_align="left",
        border_style=border_style,
        padding=(1, 2),
    )

    console.print("\n")
    console.print(panel)
    console.print()
    console.print("[#60a5fa]esprit.dev[/]")
    console.print()


def pull_docker_image() -> None:
    console = Console()
    client = check_docker_connection()
    image_name = Config.get("esprit_image")
    preferred_platform = (Config.get("esprit_docker_platform") or "").strip() or None

    # Proactively use linux/amd64 on ARM hosts (e.g. Apple Silicon) since
    # the sandbox image only publishes amd64 manifests and the Python Docker
    # SDK can hang instead of erroring when no matching manifest exists.
    if preferred_platform is None and platform.machine() in ("arm64", "aarch64"):
        preferred_platform = "linux/amd64"

    if image_exists(client, image_name):  # type: ignore[arg-type]
        return

    console.print()
    console.print(f"[dim]Pulling image[/] {image_name}")
    console.print("[dim yellow]This only happens on first run and may take a few minutes...[/]")
    console.print()

    def _pull_with_progress(progress: Progress, task_id: int, platform_override: str | None = None) -> None:
        layer_progress: dict[str, dict[str, int]] = {}
        pull_kwargs: dict[str, Any] = {"stream": True, "decode": True}
        if platform_override:
            pull_kwargs["platform"] = platform_override

        # Smoothing state for stable ETA / speed display
        _smooth_completed = 0.0
        _last_update_time = time.monotonic()
        _UPDATE_INTERVAL = 0.25  # update progress bar at most ~4 Hz
        _EMA_ALPHA = 0.3  # smoothing factor (lower = smoother)

        progress.update(
            task_id,
            description="[bold cyan]Downloading image layers...",
            total=None,
            completed=0,
            layers="0/0 layers",
        )

        for line in client.api.pull(image_name, **pull_kwargs):
            if isinstance(line, dict):
                pull_error = line.get("error")
                if pull_error:
                    error_detail = line.get("errorDetail")
                    if isinstance(error_detail, dict):
                        pull_error = error_detail.get("message", pull_error)
                    raise DockerException(str(pull_error))

                layer_id = line.get("id")
                status_text = str(line.get("status", ""))
                if isinstance(layer_id, str):
                    detail = line.get("progressDetail")
                    if not isinstance(detail, dict):
                        detail = {}

                    current_value = detail.get("current")
                    total_value = detail.get("total")

                    current = int(current_value) if isinstance(current_value, int) and current_value >= 0 else None
                    total = int(total_value) if isinstance(total_value, int) and total_value > 0 else None

                    entry = layer_progress.setdefault(layer_id, {"current": 0, "total": 0})
                    if total is not None:
                        entry["total"] = max(entry["total"], total)
                    if current is not None:
                        if entry["total"] > 0:
                            entry["current"] = min(current, entry["total"])
                        else:
                            entry["current"] = current

                    if status_text in {"Pull complete", "Already exists"} and entry["total"] > 0:
                        entry["current"] = entry["total"]
                elif status_text:
                    normalized = status_text.lower()
                    if "pulling from" in normalized:
                        progress.update(task_id, description="[bold cyan]Fetching image manifest...")
                    elif "digest:" in normalized:
                        progress.update(task_id, description="[bold cyan]Verifying image...")
                    elif "status:" in normalized:
                        progress.update(task_id, description="[bold cyan]Finalizing image...")

            now = time.monotonic()
            if now - _last_update_time < _UPDATE_INTERVAL:
                continue
            _last_update_time = now

            aggregate_current = 0
            aggregate_total = 0
            complete_layers = 0
            known_layers = 0
            for entry in layer_progress.values():
                layer_current = max(0, entry["current"])
                layer_total = max(0, entry["total"])
                if layer_total > 0:
                    known_layers += 1
                    aggregate_total += layer_total
                    aggregate_current += min(layer_current, layer_total)
                    if layer_current >= layer_total:
                        complete_layers += 1

            # Smooth the completed value to prevent jittery ETA/speed
            if aggregate_current >= aggregate_total and aggregate_total > 0:
                _smooth_completed = float(aggregate_current)
            else:
                _smooth_completed = _smooth_completed + _EMA_ALPHA * (aggregate_current - _smooth_completed)
                _smooth_completed = max(_smooth_completed, aggregate_current * 0.5)

            progress.update(
                task_id,
                total=aggregate_total if aggregate_total > 0 else None,
                completed=int(_smooth_completed),
                layers=f"{complete_layers}/{known_layers} layers" if known_layers > 0 else "resolving layers",
            )

        # Final update to ensure 100% completion is shown
        aggregate_total = sum(max(0, e["total"]) for e in layer_progress.values() if e["total"] > 0)
        if aggregate_total > 0:
            progress.update(task_id, total=aggregate_total, completed=aggregate_total)

    def _verify_image_ready(max_retries: int = 3) -> bool:
        for attempt in range(max_retries):
            try:
                client.images.get(image_name)
            except (ImageNotFound, DockerException):
                if attempt == max_retries - 1:
                    return False
                time.sleep(2**attempt)
            else:
                return True
        return False

    pull_error: DockerException | None = None
    fallback_platform: str | None = None

    with Progress(
        SpinnerColumn(style="bold cyan"),
        TextColumn("{task.description}"),
        BarColumn(bar_width=32, complete_style="cyan", finished_style="green"),
        TaskProgressColumn(),
        DownloadColumn(binary_units=True),
        TransferSpeedColumn(),
        TimeRemainingColumn(compact=True),
        TextColumn("[dim]{task.fields[layers]}[/]"),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task("[bold cyan]Downloading image layers...", total=None, layers="0/0 layers")

        try:
            _pull_with_progress(progress, task_id, preferred_platform)
        except DockerException as e:
            pull_error = e
            error_text = str(e).lower()
            manifest_mismatch = "no matching manifest" in error_text or "no match for platform" in error_text
            missing_arm_manifest = manifest_mismatch and "arm64" in error_text
            can_fallback = preferred_platform is None and missing_arm_manifest

            if can_fallback:
                fallback_platform = "linux/amd64"
                progress.update(
                    task_id,
                    description="[bold yellow]No arm64 manifest; retrying linux/amd64 emulation...[/]",
                    total=None,
                    completed=0,
                    layers="0/0 layers",
                )
                try:
                    _pull_with_progress(progress, task_id, fallback_platform)
                except DockerException as fallback_error:
                    pull_error = fallback_error
                else:
                    os.environ["ESPRIT_DOCKER_PLATFORM"] = fallback_platform
                    pull_error = None

    if pull_error is None and not _verify_image_ready():
        pull_error = DockerException(
            f"Pull completed but image is not available locally: {image_name}"
        )

    if pull_error is not None:
        console.print()
        error_text = Text()
        error_text.append("FAILED TO PULL IMAGE", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append(f"Could not download: {image_name}\n", style="white")
        if fallback_platform:
            error_text.append(
                f"Fallback pull with platform={fallback_platform} also failed.\n\n",
                style="white",
            )
        error_text.append(str(pull_error), style="dim red")

        panel = Panel(
            error_text,
            title="[bold white]ESPRIT",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )
        console.print(panel, "\n")
        sys.exit(1)

    success_text = Text()
    success_text.append("Docker image ready", style="#22c55e")
    console.print(success_text)
    if fallback_platform:
        console.print("[yellow]Using linux/amd64 sandbox image via Docker emulation.[/]")
    console.print()


def apply_config_override(config_path: str) -> None:
    Config._config_file_override = validate_config_file(config_path)
    apply_saved_config(force=True)


def persist_config() -> None:
    if Config._config_file_override is None:
        save_current_config()


def display_cost_estimate(
    model_name: str, scan_mode: str, target_count: int, is_whitebox: bool,
) -> None:
    console = Console()
    try:
        from esprit.llm.cost_estimator import estimate_scan_cost

        estimate = estimate_scan_cost(
            model_name=model_name,
            scan_mode=scan_mode,
            target_count=target_count,
            is_whitebox=is_whitebox,
        )
        if estimate["estimated_cost_mid"] > 0:
            low = estimate["estimated_cost_low"]
            high = estimate["estimated_cost_high"]
            mode = scan_mode.capitalize()
            targets = f"{target_count} target{'s' if target_count > 1 else ''}"
            wb = " + source code" if is_whitebox else ""
            console.print(
                f"[dim]Estimated cost:[/] [cyan]${low:.2f}[/] - [cyan]${high:.2f}[/] "
                f"[dim]({mode} mode, {targets}{wb})[/]"
            )
            console.print()
    except Exception:
        pass  # Non-critical — don't block scan


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    console = Console()
    args = parse_arguments()

    if args.self_update:
        import subprocess
        console.print("[bold cyan]Updating Esprit...[/]")
        try:
            if platform.system() == "Windows":
                cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-Command",
                       "irm https://raw.githubusercontent.com/esprit-cli/Esprit/main/scripts/install.ps1 | iex"]
            else:
                cmd = ["bash", "-c",
                       "curl -fsSL https://raw.githubusercontent.com/esprit-cli/Esprit/main/scripts/install.sh | bash"]
            subprocess.run(cmd, check=True)
            console.print("[green]✓ Update complete![/]")
        except subprocess.CalledProcessError:
            console.print("[red]✗ Update failed. Try manually: curl -fsSL https://raw.githubusercontent.com/esprit-cli/Esprit/main/scripts/install.sh | bash[/]")
        sys.exit(0)

    if args.config:
        apply_config_override(args.config)

    if args.command == "launchpad":
        launchpad_result = asyncio.run(run_launchpad())
        if launchpad_result is None:
            return
        if not _apply_launchpad_result(args, launchpad_result):
            return

    # Interactive pre-scan checks: provider, model, account selection.
    # Launchpad now owns this flow when a scan is started from its UI.
    if not getattr(args, "skip_pre_scan_checks", False):
        if not pre_scan_setup(non_interactive=args.non_interactive):
            sys.exit(1)

    use_cloud_runtime = _should_use_cloud_runtime()
    if use_cloud_runtime:
        os.environ["ESPRIT_RUNTIME_BACKEND"] = "cloud"
        configured_model = (Config.get("esprit_llm") or "").strip()
        if configured_model.lower().startswith("bedrock/"):
            os.environ["ESPRIT_LLM"] = "esprit/default"
            Config.save_current()

        console.print("[green]\u2713[/] Using Esprit Cloud (no Docker required)")

        from esprit.auth.credentials import verify_subscription

        verification = verify_subscription()
        for line in build_subscription_quota_lines(verification):
            console.print(line)
        console.print()
    else:
        os.environ["ESPRIT_RUNTIME_BACKEND"] = "docker"

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            init_task = progress.add_task("Initializing scan...", total=4)

            # Step 1: Check Docker installation
            progress.update(init_task, description="Checking Docker installation...")
            check_docker_installed()
            progress.advance(init_task)

            # Step 2: Ensure Docker engine is running
            progress.update(init_task, description="Starting Docker engine...")
            progress.stop()
            ensure_docker_running()
            progress.start()
            progress.advance(init_task)

            # Step 3: Docker health check
            progress.update(init_task, description="Running Docker health check...")
            progress.stop()
            if not _docker_health_check(console, Config):
                sys.exit(1)
            progress.start()
            progress.advance(init_task)

            # Step 4: Pull/verify sandbox image
            progress.update(init_task, description="Verifying sandbox image...")
            progress.stop()
            pull_docker_image()
            progress.start()
            progress.advance(init_task)

    validate_environment()
    asyncio.run(warm_up_llm())

    persist_config()

    args.run_name = generate_run_name(args.targets_info)

    for target_info in args.targets_info:
        if target_info["type"] == "repository":
            repo_url = target_info["details"]["target_repo"]
            dest_name = target_info["details"].get("workspace_subdir")
            cloned_path = clone_repository(repo_url, args.run_name, dest_name)
            target_info["details"]["cloned_repo_path"] = cloned_path

    args.local_sources = collect_local_sources(args.targets_info)

    is_whitebox = bool(args.local_sources)

    display_cost_estimate(
        model_name=Config.get("esprit_llm") or "",
        scan_mode=args.scan_mode,
        target_count=len(args.targets_info),
        is_whitebox=is_whitebox,
    )

    posthog.start(
        model=Config.get("esprit_llm"),
        scan_mode=args.scan_mode,
        is_whitebox=is_whitebox,
        interactive=not args.non_interactive,
        has_instructions=bool(args.instruction),
    )

    # Apply any update that was scheduled on the previous run.
    # This runs in the terminal before Textual starts, so the install script
    # output is visible.  apply_update() re-execs on success, so we never
    # reach the lines below when an update is pending.
    if has_pending_update():
        console = Console()
        console.print("\n[bold cyan]Applying scheduled update…[/bold cyan]")
        apply_update(restart=True)
        # If we reach here the update failed (non-zero exit); continue normally.

    exit_reason = "user_exit"
    tui_result = None
    try:
        # Create GUI server (always available — serves live dashboard on localhost:7860)
        gui_server = None
        try:
            from esprit.gui import GUIServer

            gui_server = GUIServer(port=7860)
        except ImportError:
            pass

        if args.non_interactive:
            asyncio.run(run_cli(args))
        else:
            tui_result = asyncio.run(run_tui(args, gui_server=gui_server))
    except KeyboardInterrupt:
        exit_reason = "interrupted"

        # Force-quit watchdog: if cleanup hangs longer than 10s, bail out.
        def _force_quit() -> None:
            console.print("\n[bold red]Force quitting...[/bold red]")
            os._exit(1)

        watchdog = threading.Timer(10.0, _force_quit)
        watchdog.daemon = True
        watchdog.start()

        with console.status(
            "[bold yellow]⏳ Cancelling scan... saving partial results[/bold yellow]",
            spinner="dots",
        ):
            tracer = get_global_tracer()
            if tracer:
                posthog.end(tracer, exit_reason=exit_reason)

        watchdog.cancel()

        results_path = Path("esprit_runs") / args.run_name
        if results_path.exists():
            console.print(
                f"[green]✓[/green] Scan cancelled. Partial results saved to [bold]{results_path}[/bold]"
            )
        else:
            console.print("[green]✓[/green] Scan cancelled.")
        return
    except Exception as e:
        exit_reason = "error"
        posthog.error("unhandled_exception", str(e))
        raise
    finally:
        tracer = get_global_tracer()
        if tracer and exit_reason != "interrupted":
            posthog.end(tracer, exit_reason=exit_reason)

    # "Update Now" was chosen inside the TUI — apply immediately now that
    # Textual has fully released the terminal.
    if tui_result == "update_now":
        console = Console()
        console.print("\n[bold cyan]Downloading and installing update…[/bold cyan]")
        apply_update(restart=True)
        # If we reach here the update failed; fall through to completion message.

    results_path = Path("esprit_runs") / args.run_name
    display_completion_message(args, results_path)

    if args.non_interactive:
        tracer = get_global_tracer()
        if tracer and tracer.vulnerability_reports:
            sys.exit(2)


if __name__ == "__main__":
    main()
