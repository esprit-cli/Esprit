#!/usr/bin/env python3
"""
Esprit Agent Interface

Commands:
  esprit scan <target>       Run a penetration test scan
  esprit provider login      Login to an LLM provider (OAuth)
  esprit provider status     Show provider authentication status
  esprit provider logout     Logout from a provider
"""

import argparse
import asyncio
import logging
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import litellm
from docker.errors import DockerException
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from esprit.config import Config, apply_saved_config, save_current_config


apply_saved_config()

from esprit.interface.cli import run_cli  # noqa: E402
from esprit.interface.launchpad import LaunchpadResult, run_launchpad  # noqa: E402
from esprit.interface.tui import run_tui  # noqa: E402
from esprit.interface.utils import (  # noqa: E402
    assign_workspace_subdirs,
    build_final_stats_text,
    check_docker_connection,
    clone_repository,
    collect_local_sources,
    generate_run_name,
    image_exists,
    infer_target_type,
    get_severity_color,
    process_pull_line,
    rewrite_localhost_targets,
    validate_config_file,
    validate_llm_response,
)
from esprit.runtime.docker_runtime import HOST_GATEWAY_HOSTNAME  # noqa: E402
from esprit.telemetry import posthog  # noqa: E402
from esprit.telemetry.tracer import get_global_tracer  # noqa: E402


logging.getLogger().setLevel(logging.ERROR)


def _video_export_dependency_issue() -> str | None:
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return (
            "playwright is not installed. Install with: "
            "pip install 'esprit-cli[video]' && python -m playwright install chromium"
        )

    if shutil.which("ffmpeg") is None:
        return "ffmpeg is not installed or not on PATH."

    return None


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
        with console.status("[bold cyan]Waiting for Docker to start...", spinner="dots"):
            for _ in range(60):  # Wait up to 60 seconds
                time.sleep(1)
                try:
                    import docker as docker_lib
                    docker_lib.from_env()
                    console.print("[green]Docker started.[/]")
                    return
                except Exception:
                    continue

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

    # Check for OAuth providers (single-credential)
    token_store = TokenStore()
    for provider_id in ["anthropic", "google", "github-copilot"]:
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

    return False


def _get_configured_providers() -> list[tuple[str, str]]:
    """Return list of (provider_id, detail) for all configured providers."""
    from esprit.providers.token_store import TokenStore
    from esprit.providers.account_pool import get_account_pool

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

    for provider_id in ["antigravity", "openai", "anthropic", "google", "github-copilot"]:
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
            if token_store.has_credentials(provider_id):
                creds = token_store.get(provider_id)
                detail = creds.type.upper() if creds else "configured"
                result.append((provider_id, detail))

    # Direct API key (provider-agnostic)
    if Config.get("llm_api_key"):
        result.append(("direct", "LLM_API_KEY env"))

    return result


def _get_available_models(configured_providers: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Return list of (model_id, display_name) available from configured providers."""
    from esprit.providers.config import AVAILABLE_MODELS

    provider_ids = {p[0] for p in configured_providers}
    models = []

    for provider_id, model_list in AVAILABLE_MODELS.items():
        if provider_id in provider_ids:
            for entry in model_list:
                model_id, display_name = entry[0], entry[1]
                full_id = f"{provider_id}/{model_id}"
                models.append((full_id, f"{display_name} [{provider_id}]"))

    # If direct API key is configured, any model might work
    if "direct" in provider_ids:
        current = Config.get("esprit_llm")
        if current and not any(m[0] == current for m in models):
            models.append((current, f"{current} [direct API key]"))

    return models


def _non_interactive_model_rank(model_id: str) -> tuple[int, str]:
    """Rank model IDs for safer non-interactive defaults."""
    model_lower = model_id.lower()
    provider = model_lower.split("/", 1)[0] if "/" in model_lower else ""

    score = 50
    if provider == "esprit":
        score = 0
    elif "haiku" in model_lower:
        score = 5
    elif "mini" in model_lower or "flash" in model_lower:
        score = 10
    elif "sonnet" in model_lower:
        score = 20
    elif "gpt-5" in model_lower:
        score = 30
    elif "opus" in model_lower:
        score = 40

    # OpenAI OAuth Codex models are frequently mis-scoped for Responses API write.
    if provider == "openai" and "codex" in model_lower:
        score += 20

    return (score, model_lower)


def _pick_auto_model(available_models: list[tuple[str, str]]) -> str | None:
    if not available_models:
        return None
    ranked = sorted(available_models, key=lambda item: _non_interactive_model_rank(item[0]))
    return ranked[0][0]


def _warmup_fallback_models(current_model: str | None) -> list[str]:
    providers = _get_configured_providers()
    available_models = _get_available_models(providers)
    ranked_models = [m[0] for m in sorted(available_models, key=lambda item: _non_interactive_model_rank(item[0]))]

    ordered: list[str] = []
    if current_model:
        ordered.append(current_model)

    for model_id in ranked_models:
        if model_id not in ordered:
            ordered.append(model_id)

    return ordered


def pre_scan_setup(non_interactive: bool = False, args: "argparse.Namespace | None" = None) -> bool:
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
        console.print("  [cyan]esprit provider login[/]          # OAuth (Codex, Copilot, Antigravity)")
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
            "openai": "OpenAI",
            "anthropic": "Anthropic",
            "google": "Google",
            "github-copilot": "GitHub Copilot",
            "direct": "Direct",
        }.get(pid, pid)
        auth_type = {
            "esprit": "[green]Platform[/]",
            "antigravity": "[green]OAuth[/]",
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
            selected_model = _pick_auto_model(available_models)
            if not selected_model:
                console.print("[red]Could not auto-select a model.[/]")
                console.print()
                return False
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

    # Show selection-time estimate (depends on selected model and scan mode)
    if args is not None and current_model:
        try:
            from esprit.llm.cost_estimator import estimate_scan_profile

            target_count = len(getattr(args, "targets_info", []) or [])
            is_whitebox = any(
                t.get("type") in {"repository", "local_code"}
                for t in (getattr(args, "targets_info", []) or [])
                if isinstance(t, dict)
            )
            estimate = estimate_scan_profile(
                model_name=current_model,
                scan_mode=getattr(args, "scan_mode", "deep"),
                target_count=max(1, target_count),
                is_whitebox=is_whitebox,
            )
            console.print(
                "[dim]Selection estimate:[/] "
                f"[cyan]${estimate['estimated_cost_low']:.2f}-${estimate['estimated_cost_high']:.2f}[/] "
                f"[dim]·[/] [cyan]~{int(round(estimate['estimated_time_low_min']))}-"
                f"{int(round(estimate['estimated_time_high_min']))} min[/]"
            )
        except Exception:
            pass

    console.print()

    # --- Step 4: Scan goal (optional) ---
    if not non_interactive and args is not None:
        existing_instruction = getattr(args, "instruction", None)
        if not existing_instruction:
            console.print(
                "[bold]Do you have a specific goal?[/] "
                "[dim](e.g. 'find auth bypass vulnerabilities', 'test the API endpoints')[/]"
            )
            goal = Prompt.ask(
                "[bold]Goal[/]",
                default="general pentest",
            )
            if goal and goal.strip().lower() not in ("", "general pentest", "no", "none", "n/a"):
                args.instruction = goal.strip()
                console.print(f"[dim]Goal:[/] [#22d3ee]{args.instruction}[/]")
            else:
                console.print("[dim]Goal:[/] general penetration test")
            console.print()

    # --- Step 5: Confirm ---
    if not non_interactive:
        if not Confirm.ask("[bold]Proceed with scan?[/]", default=True):
            console.print("[dim]Scan cancelled.[/]")
            return False

    # --- Step 6: Video recording settings ---
    if args is not None:
        if non_interactive:
            # Non-interactive: no video by default
            args.video_enabled = False
            args.video_speed = 10.0
            args.video_resolution = (1920, 1080)
            args.video_output = None
        else:
            console.print()
            record_video = Confirm.ask(
                "[bold]Record video replay of this scan?[/]", default=True
            )
            args.video_enabled = record_video

            if record_video:
                # Speed selection
                console.print()
                console.print("[bold]Video speed:[/]")
                console.print("  1. [cyan]5x[/]   — slow, detailed")
                console.print("  2. [cyan]10x[/]  — balanced [dim](default)[/]")
                console.print("  3. [cyan]20x[/]  — fast overview")
                console.print("  4. [cyan]50x[/]  — ultra fast")
                speed_choice = Prompt.ask(
                    "Select speed",
                    choices=["1", "2", "3", "4"],
                    default="2",
                )
                speed_map = {"1": 5.0, "2": 10.0, "3": 20.0, "4": 50.0}
                args.video_speed = speed_map[speed_choice]

                # Resolution selection
                res_choice = Prompt.ask(
                    "[bold]Resolution[/]",
                    choices=["1080p", "720p"],
                    default="1080p",
                )
                res_map = {"1080p": (1920, 1080), "720p": (1280, 720)}
                args.video_resolution = res_map[res_choice]

                # Save path
                default_path = "esprit_runs/<run>/replay.mp4"
                custom_path = Prompt.ask(
                    "[bold]Save video to[/]",
                    default=default_path,
                )
                if custom_path == default_path:
                    args.video_output = None  # Will use default run dir
                else:
                    args.video_output = custom_path
                dep_issue = _video_export_dependency_issue()
                if dep_issue:
                    console.print(
                        f"[yellow]Video export dependency check:[/] {dep_issue}"
                    )

                speed_label = f"{args.video_speed:.0f}x"
                console.print(
                    f"[dim]Video:[/] {speed_label} · {res_choice}"
                    + (f" · {custom_path}" if args.video_output else "")
                )
            else:
                args.video_speed = 10.0
                args.video_resolution = (1920, 1080)
                args.video_output = None

    console.print()
    return True


async def warm_up_llm() -> None:
    from esprit.llm.config import DEFAULT_MODEL
    from esprit.llm.api_base import (
        configured_api_base,
        detect_conflicting_provider_base_env,
    )
    from esprit.llm.completion_args import build_completion_args

    console = Console()
    model_name = ""

    try:
        model_name = Config.get("esprit_llm") or DEFAULT_MODEL
        model_lower = model_name.lower() if model_name else ""

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
        warmup_timeout = min(llm_timeout, 45)
        candidates = _warmup_fallback_models(model_name)
        max_candidates = 4
        last_error: Exception | None = None

        for idx, candidate_model in enumerate(candidates[:max_candidates]):
            try:
                completion_kwargs = build_completion_args(
                    model_name=candidate_model,
                    messages=test_messages,
                    timeout=warmup_timeout,
                )
                response = litellm.completion(**completion_kwargs)
                validate_llm_response(response)

                if candidate_model != model_name:
                    os.environ["ESPRIT_LLM"] = candidate_model
                    Config.save_current()
                    console.print(
                        "[yellow]Selected model was unavailable. "
                        f"Falling back to: {candidate_model}[/]"
                    )
                return
            except Exception as e:  # noqa: BLE001
                last_error = e
                if idx < min(len(candidates[:max_candidates]), max_candidates) - 1:
                    console.print(
                        f"[dim]Warm-up failed for {candidate_model}; trying fallback model...[/]"
                    )
                continue

        if last_error:
            raise last_error

    except Exception as e:  # noqa: BLE001
        error_detail = str(e)
        error_detail_lower = error_detail.lower()
        model_lower = (Config.get("esprit_llm") or DEFAULT_MODEL).lower()
        if (
            "nonetype' is not iterable" in error_detail_lower
            and "codex" in model_lower
            and "openai" in model_lower
        ):
            error_detail = (
                "OpenAI credentials are not authorized for this Codex model. "
                "This usually means missing `api.responses.write` scope or "
                "insufficient project role permissions. "
                "Use an OpenAI API key with Responses API write access, "
                "or run `esprit provider login openai` and re-authenticate."
            )

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
        error_text.append(f"\nError: {error_detail}", style="dim white")

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
  esprit provider login      Login to an LLM provider (OAuth)
  esprit provider status     Show provider authentication status

Examples:
  # Run a scan
  esprit scan https://example.com
  esprit scan github.com/user/repo
  esprit scan ./my-project

  # Provider authentication
  esprit provider login              # Interactive provider selection
  esprit provider login esprit       # Login with Esprit subscription
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
  anthropic       Claude Pro/Max (OAuth) or API key
  openai          ChatGPT Plus/Pro / Codex (OAuth) or API key
  github-copilot  GitHub Copilot (OAuth)
  google          Google Gemini (OAuth) or API key
        """,
    )
    provider_subparsers = provider_parser.add_subparsers(dest="provider_command")
    
    provider_login = provider_subparsers.add_parser("login", help="Login to a provider via OAuth")
    provider_login.add_argument("provider_id", nargs="?", help="Provider ID (esprit, anthropic, openai, github-copilot, google)")
    
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
    scan_parser.add_argument("--resume", type=str, help="Path to checkpoint file to resume from")

    # Report subcommand
    report_parser = subparsers.add_parser(
        "report",
        help="Generate reports from a completed scan",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    report_parser.add_argument("run_id", help="Run ID or path to run directory")
    report_parser.add_argument("--html", action="store_true", help="Generate HTML report")
    report_parser.add_argument("--timelapse", action="store_true", help="Generate timelapse")
    report_parser.add_argument("--output", "-o", type=str, help="Output directory for reports")
    report_parser.add_argument(
        "--video",
        action="store_true",
        help="Export scan replay as MP4 video",
    )
    report_parser.add_argument(
        "--speed",
        type=float,
        default=10.0,
        help="Video playback speed multiplier (default: 10)",
    )
    report_parser.add_argument(
        "--resolution",
        choices=["1080p", "720p"],
        default="1080p",
        help="Video resolution (default: 1080p)",
    )

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

    # Handle report subcommand
    if args.command == "report":
        _cmd_report(args)
        sys.exit(0)

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

    # Show goal if one was set
    goal_instruction = getattr(args, "instruction", None)
    goal_parts: list[Any] = []
    if goal_instruction:
        goal_text = Text()
        goal_text.append("\n")
        goal_text.append("Goal", style="dim")
        goal_text.append("    ")
        goal_text.append(goal_instruction, style="#22d3ee")
        goal_parts = ["\n", goal_text]

    stats_text = build_final_stats_text(tracer)

    panel_parts = [completion_text, "\n\n", target_text]
    panel_parts.extend(goal_parts)

    if stats_text.plain:
        panel_parts.extend(["\n", stats_text])

    if scan_completed or has_vulnerabilities:
        results_text = Text()
        results_text.append("\n")
        results_text.append("Output", style="dim")
        results_text.append("  ")
        results_text.append(str(results_path), style="#60a5fa")
        panel_parts.extend(["\n", results_text])

    if tracer and tracer.vulnerability_reports:
        vuln_text = Text()
        vuln_text.append("\n")
        vuln_text.append("Vulnerabilities", style="dim")
        for report in tracer.vulnerability_reports:
            title = str(report.get("title", "Untitled"))
            severity = str(report.get("severity", "info")).lower()
            color = get_severity_color(severity)
            vuln_text.append("\n  ")
            vuln_text.append(f"{severity.upper():<8}", style=f"bold {color}")
            vuln_text.append("  ")
            vuln_text.append(title, style="white")
        panel_parts.extend(["\n", vuln_text])

    if scan_completed or has_vulnerabilities:
        artifacts = [
            results_path / "penetration_test_report.md",
            results_path / "vulnerabilities.csv",
            results_path / "vulnerabilities",
            results_path / "replay.mp4",
            results_path / "checkpoint.json",
            results_path / "run.log",
        ]
        artifacts_text = Text()
        first = True
        for artifact_path in artifacts:
            if artifact_path.exists():
                if first:
                    artifacts_text.append("\n")
                    artifacts_text.append("Saved", style="dim")
                    first = False
                artifacts_text.append("\n  ")
                artifacts_text.append(str(artifact_path), style="#60a5fa")
        if not first:
            panel_parts.extend(["\n", artifacts_text])

    # Show video save path if auto-export was configured
    video_path = getattr(args, "video_saved_path", None)
    if video_path:
        video_text = Text()
        video_text.append("\n")
        video_text.append("Video", style="dim")
        video_text.append("   ")
        video_text.append(str(video_path), style="#a78bfa")
        panel_parts.extend(["\n", video_text])
    elif getattr(args, "video_enabled", False):
        video_text = Text()
        video_text.append("\n")
        video_text.append("Video", style="dim")
        video_text.append("   ")
        video_text.append("export may still be running in background", style="dim #fbbf24")
        panel_parts.extend(["\n", video_text])

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

    if image_exists(client, Config.get("esprit_image")):  # type: ignore[arg-type]
        return

    console.print()
    console.print(f"[dim]Pulling image[/] {Config.get('esprit_image')}")
    console.print("[dim yellow]This only happens on first run and may take a few minutes...[/]")
    console.print()

    with console.status("[bold cyan]Downloading image layers...", spinner="dots") as status:
        try:
            layers_info: dict[str, str] = {}
            last_update = ""

            for line in client.api.pull(Config.get("esprit_image"), stream=True, decode=True):
                last_update = process_pull_line(line, layers_info, status, last_update)

        except DockerException as e:
            console.print()
            error_text = Text()
            error_text.append("FAILED TO PULL IMAGE", style="bold red")
            error_text.append("\n\n", style="white")
            error_text.append(f"Could not download: {Config.get('esprit_image')}\n", style="white")
            error_text.append(str(e), style="dim red")

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
        from esprit.llm.cost_estimator import estimate_scan_profile

        estimate = estimate_scan_profile(
            model_name=model_name,
            scan_mode=scan_mode,
            target_count=target_count,
            is_whitebox=is_whitebox,
        )
        if estimate["estimated_cost_mid"] > 0:
            low = estimate["estimated_cost_low"]
            high = estimate["estimated_cost_high"]
            time_low = estimate.get("estimated_time_low_min")
            time_high = estimate.get("estimated_time_high_min")
            mode = scan_mode.capitalize()
            targets = f"{target_count} target{'s' if target_count > 1 else ''}"
            wb = " + source code" if is_whitebox else ""
            console.print(
                f"[dim]Estimated cost:[/] [cyan]${low:.2f}[/] - [cyan]${high:.2f}[/] "
                f"[dim]({mode} mode, {targets}{wb})[/]"
            )
            if isinstance(time_low, (int, float)) and isinstance(time_high, (int, float)):
                console.print(
                    f"[dim]Estimated time:[/] [cyan]~{int(round(time_low))}-{int(round(time_high))} min[/]"
                )
            console.print()
    except Exception:
        pass  # Non-critical — don't block scan


def _cmd_report(args: argparse.Namespace) -> None:
    """Handle the ``esprit report`` subcommand."""
    console = Console()

    run_dir = Path("esprit_runs") / args.run_id
    if not run_dir.exists():
        run_dir = Path(args.run_id)

    if not run_dir.exists():
        console.print(f"[red]Run directory not found:[/] {run_dir}")
        sys.exit(1)

    from esprit.telemetry.tracer import Tracer

    tracer = Tracer.load_from_dir(run_dir)
    if tracer is None:
        console.print(f"[red]Could not load tracer data from:[/] {run_dir}")
        sys.exit(1)

    if getattr(args, "video", False):
        resolution_map = {"1080p": (1920, 1080), "720p": (1280, 720)}
        resolution = resolution_map.get(getattr(args, "resolution", "1080p"), (1920, 1080))
        speed = getattr(args, "speed", 10.0)
        output_arg = getattr(args, "output", None)
        output = Path(output_arg) if output_arg else run_dir / "replay.mp4"
        try:
            from esprit.reporting.video_exporter import MissingDependencyError, VideoExporter

            video_exporter = VideoExporter(tracer)
            with console.status("[cyan]Rendering video…"):
                out = video_exporter.export_video(output, speed=speed, resolution=resolution)
            console.print(f"[green]Video:[/] {out}")
        except MissingDependencyError as e:
            console.print(f"[red]Missing dependency:[/] {e}")
            sys.exit(1)


def main() -> None:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    args = parse_arguments()

    if args.config:
        apply_config_override(args.config)

    if args.command == "launchpad":
        launchpad_result = asyncio.run(run_launchpad())
        if launchpad_result is None:
            return
        if not _apply_launchpad_result(args, launchpad_result):
            return

    check_docker_installed()
    ensure_docker_running()
    pull_docker_image()

    # Interactive pre-scan checks: provider, model, account selection.
    # Launchpad now owns this flow when a scan is started from its UI.
    if not getattr(args, "skip_pre_scan_checks", False):
        if not pre_scan_setup(non_interactive=args.non_interactive, args=args):
            sys.exit(1)

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

    exit_reason = "user_exit"
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
            asyncio.run(run_tui(args, gui_server=gui_server))
    except KeyboardInterrupt:
        exit_reason = "interrupted"
    except Exception as e:
        exit_reason = "error"
        posthog.error("unhandled_exception", str(e))
        raise
    finally:
        tracer = get_global_tracer()
        if tracer:
            posthog.end(tracer, exit_reason=exit_reason)

    results_path = Path("esprit_runs") / args.run_name
    display_completion_message(args, results_path)

    if args.non_interactive:
        tracer = get_global_tracer()
        if tracer and tracer.vulnerability_reports:
            sys.exit(2)


if __name__ == "__main__":
    main()
