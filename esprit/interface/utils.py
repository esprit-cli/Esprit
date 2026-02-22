import ipaddress
import json
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import docker
from docker.errors import DockerException, ImageNotFound
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from esprit.interface.theme_tokens import get_marker_color, get_theme_tokens


# Token formatting utilities
def format_token_count(count: float) -> str:
    count = int(count)
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


# Display utilities
def get_severity_color(severity: str) -> str:
    severity_colors = {
        "critical": "#dc2626",
        "high": "#ea580c",
        "medium": "#d97706",
        "low": "#65a30d",
        "info": "#0284c7",
    }
    return severity_colors.get(severity, "#6b7280")


def get_cvss_color(cvss_score: float) -> str:
    if cvss_score >= 9.0:
        return "#dc2626"
    if cvss_score >= 7.0:
        return "#ea580c"
    if cvss_score >= 4.0:
        return "#d97706"
    if cvss_score >= 0.1:
        return "#65a30d"
    return "#6b7280"


def format_vulnerability_report(report: dict[str, Any]) -> Text:  # noqa: PLR0912, PLR0915
    """Format a vulnerability report for CLI display with all rich fields."""
    field_style = "bold #4ade80"

    text = Text()

    title = report.get("title", "")
    if title:
        text.append("Vulnerability Report", style="bold #ea580c")
        text.append("\n\n")
        text.append("Title: ", style=field_style)
        text.append(title)

    severity = report.get("severity", "")
    if severity:
        text.append("\n\n")
        text.append("Severity: ", style=field_style)
        severity_color = get_severity_color(severity.lower())
        text.append(severity.upper(), style=f"bold {severity_color}")

    cvss = report.get("cvss")
    if cvss is not None:
        text.append("\n\n")
        text.append("CVSS Score: ", style=field_style)
        cvss_color = get_cvss_color(cvss)
        text.append(f"{cvss:.1f}", style=f"bold {cvss_color}")

    target = report.get("target")
    if target:
        text.append("\n\n")
        text.append("Target: ", style=field_style)
        text.append(target)

    endpoint = report.get("endpoint")
    if endpoint:
        text.append("\n\n")
        text.append("Endpoint: ", style=field_style)
        text.append(endpoint)

    method = report.get("method")
    if method:
        text.append("\n\n")
        text.append("Method: ", style=field_style)
        text.append(method)

    cve = report.get("cve")
    if cve:
        text.append("\n\n")
        text.append("CVE: ", style=field_style)
        text.append(cve)

    cvss_breakdown = report.get("cvss_breakdown", {})
    if cvss_breakdown:
        text.append("\n\n")
        cvss_parts = []
        if cvss_breakdown.get("attack_vector"):
            cvss_parts.append(f"AV:{cvss_breakdown['attack_vector']}")
        if cvss_breakdown.get("attack_complexity"):
            cvss_parts.append(f"AC:{cvss_breakdown['attack_complexity']}")
        if cvss_breakdown.get("privileges_required"):
            cvss_parts.append(f"PR:{cvss_breakdown['privileges_required']}")
        if cvss_breakdown.get("user_interaction"):
            cvss_parts.append(f"UI:{cvss_breakdown['user_interaction']}")
        if cvss_breakdown.get("scope"):
            cvss_parts.append(f"S:{cvss_breakdown['scope']}")
        if cvss_breakdown.get("confidentiality"):
            cvss_parts.append(f"C:{cvss_breakdown['confidentiality']}")
        if cvss_breakdown.get("integrity"):
            cvss_parts.append(f"I:{cvss_breakdown['integrity']}")
        if cvss_breakdown.get("availability"):
            cvss_parts.append(f"A:{cvss_breakdown['availability']}")
        if cvss_parts:
            text.append("CVSS Vector: ", style=field_style)
            text.append("/".join(cvss_parts), style="dim")

    description = report.get("description")
    if description:
        text.append("\n\n")
        text.append("Description", style=field_style)
        text.append("\n")
        text.append(description)

    impact = report.get("impact")
    if impact:
        text.append("\n\n")
        text.append("Impact", style=field_style)
        text.append("\n")
        text.append(impact)

    technical_analysis = report.get("technical_analysis")
    if technical_analysis:
        text.append("\n\n")
        text.append("Technical Analysis", style=field_style)
        text.append("\n")
        text.append(technical_analysis)

    poc_description = report.get("poc_description")
    if poc_description:
        text.append("\n\n")
        text.append("PoC Description", style=field_style)
        text.append("\n")
        text.append(poc_description)

    poc_script_code = report.get("poc_script_code")
    if poc_script_code:
        text.append("\n\n")
        text.append("PoC Code", style=field_style)
        text.append("\n")
        text.append(poc_script_code, style="dim")

    code_file = report.get("code_file")
    if code_file:
        text.append("\n\n")
        text.append("Code File: ", style=field_style)
        text.append(code_file)

    code_before = report.get("code_before")
    if code_before:
        text.append("\n\n")
        text.append("Code Before", style=field_style)
        text.append("\n")
        text.append(code_before, style="dim")

    code_after = report.get("code_after")
    if code_after:
        text.append("\n\n")
        text.append("Code After", style=field_style)
        text.append("\n")
        text.append(code_after, style="dim")

    code_diff = report.get("code_diff")
    if code_diff:
        text.append("\n\n")
        text.append("Code Diff", style=field_style)
        text.append("\n")
        text.append(code_diff, style="dim")

    remediation_steps = report.get("remediation_steps")
    if remediation_steps:
        text.append("\n\n")
        text.append("Remediation", style=field_style)
        text.append("\n")
        text.append(remediation_steps)

    return text


def _build_vulnerability_stats(stats_text: Text, tracer: Any) -> None:
    """Build vulnerability section of stats text."""
    vuln_count = len(tracer.vulnerability_reports)

    if vuln_count > 0:
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for report in tracer.vulnerability_reports:
            severity = report.get("severity", "").lower()
            if severity in severity_counts:
                severity_counts[severity] += 1

        stats_text.append("Vulnerabilities  ", style="bold red")

        severity_parts = []
        for severity in ["critical", "high", "medium", "low", "info"]:
            count = severity_counts[severity]
            if count > 0:
                severity_color = get_severity_color(severity)
                severity_text = Text()
                severity_text.append(f"{severity.upper()}: ", style=severity_color)
                severity_text.append(str(count), style=f"bold {severity_color}")
                severity_parts.append(severity_text)

        for i, part in enumerate(severity_parts):
            stats_text.append(part)
            if i < len(severity_parts) - 1:
                stats_text.append(" | ", style="dim white")

        stats_text.append(" (Total: ", style="dim white")
        stats_text.append(str(vuln_count), style="bold yellow")
        stats_text.append(")", style="dim white")
        stats_text.append("\n")
    else:
        stats_text.append("Vulnerabilities  ", style="bold #22c55e")
        stats_text.append("0", style="bold white")
        stats_text.append(" (No exploitable vulnerabilities detected)", style="dim green")
        stats_text.append("\n")


def _build_llm_stats(stats_text: Text, total_stats: dict[str, Any]) -> None:
    """Build LLM usage section of stats text."""
    if total_stats["requests"] > 0:
        stats_text.append("\n")
        stats_text.append("Input Tokens ", style="dim")
        stats_text.append(format_token_count(total_stats["input_tokens"]), style="white")

        if total_stats["cached_tokens"] > 0:
            stats_text.append("  ·  ", style="dim white")
            stats_text.append("Cached Tokens ", style="dim")
            stats_text.append(format_token_count(total_stats["cached_tokens"]), style="white")

        stats_text.append("  ·  ", style="dim white")
        stats_text.append("Output Tokens ", style="dim")
        stats_text.append(format_token_count(total_stats["output_tokens"]), style="white")

        cost = total_stats["cost"]
        stats_text.append(" · ", style="dim white")
        stats_text.append("Cost ", style="dim")
        if cost >= 0.005:
            stats_text.append(f"${cost:.2f}", style="bold #fbbf24")
        else:
            stats_text.append(f"${cost:.2f}", style="dim white")
    else:
        stats_text.append("\n")
        stats_text.append("Cost ", style="dim")
        stats_text.append("$0.00 ", style="dim white")
        stats_text.append("· ", style="dim white")
        stats_text.append("Tokens ", style="dim")
        stats_text.append("0", style="white")


def build_final_stats_text(tracer: Any) -> Text:
    """Build stats text for final output with detailed messages and LLM usage."""
    stats_text = Text()
    if not tracer:
        return stats_text

    _build_vulnerability_stats(stats_text, tracer)

    tool_count = tracer.get_real_tool_count()
    agent_count = len(tracer.agents)

    stats_text.append("Agents", style="dim")
    stats_text.append("  ")
    stats_text.append(str(agent_count), style="bold white")
    stats_text.append("  ·  ", style="dim white")
    stats_text.append("Tools", style="dim")
    stats_text.append("  ")
    stats_text.append(str(tool_count), style="bold white")

    llm_stats = tracer.get_total_llm_stats()
    _build_llm_stats(stats_text, llm_stats["total"])

    return stats_text


def build_live_stats_text(tracer: Any, agent_config: dict[str, Any] | None = None) -> Text:
    stats_text = Text()
    if not tracer:
        return stats_text

    if agent_config:
        llm_config = agent_config["llm_config"]
        model = getattr(llm_config, "model_name", "Unknown")
        stats_text.append("Model ", style="dim")
        stats_text.append(model, style="white")
        stats_text.append("\n")

    vuln_count = len(tracer.vulnerability_reports)
    tool_count = tracer.get_real_tool_count()
    agent_count = len(tracer.agents)

    stats_text.append("Vulnerabilities ", style="dim")
    stats_text.append(f"{vuln_count}", style="white")
    stats_text.append("\n")
    if vuln_count > 0:
        severity_counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
        for report in tracer.vulnerability_reports:
            severity = report.get("severity", "").lower()
            if severity in severity_counts:
                severity_counts[severity] += 1

        severity_parts = []
        for severity in ["critical", "high", "medium", "low", "info"]:
            count = severity_counts[severity]
            if count > 0:
                severity_color = get_severity_color(severity)
                severity_text = Text()
                severity_text.append(f"{severity.upper()}: ", style=severity_color)
                severity_text.append(str(count), style=f"bold {severity_color}")
                severity_parts.append(severity_text)

        for i, part in enumerate(severity_parts):
            stats_text.append(part)
            if i < len(severity_parts) - 1:
                stats_text.append(" | ", style="dim white")

        stats_text.append("\n")

    stats_text.append("Agents ", style="dim")
    stats_text.append(str(agent_count), style="white")
    stats_text.append("  ·  ", style="dim white")
    stats_text.append("Tools ", style="dim")
    stats_text.append(str(tool_count), style="white")

    llm_stats = tracer.get_total_llm_stats()
    total_stats = llm_stats["total"]

    stats_text.append("\n")

    stats_text.append("Input Tokens ", style="dim")
    stats_text.append(format_token_count(total_stats["input_tokens"]), style="white")

    stats_text.append("  ·  ", style="dim white")
    stats_text.append("Cached Tokens ", style="dim")
    stats_text.append(format_token_count(total_stats["cached_tokens"]), style="white")

    stats_text.append("\n")

    stats_text.append("Output Tokens ", style="dim")
    stats_text.append(format_token_count(total_stats["output_tokens"]), style="white")

    stats_text.append("  ·  ", style="dim white")

    # Cost
    model = ""
    if agent_config:
        llm_config = agent_config["llm_config"]
        model = getattr(llm_config, "model_name", "")

    from esprit.llm.pricing import get_pricing_db

    session_cost = get_pricing_db().get_cost(
        model,
        total_stats["input_tokens"],
        total_stats["output_tokens"],
        total_stats["cached_tokens"],
    ) if model else 0.0

    stats_text.append("Cost ", style="dim")
    if session_cost >= 0.005:
        stats_text.append(f"${session_cost:.2f}", style="#fbbf24")
    else:
        stats_text.append(f"${session_cost:.2f}", style="dim white")

    return stats_text


_SCAN_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

_ACTIVITY_SPINNER = ["◐", "◓", "◑", "◒"]


def _format_elapsed(seconds: float) -> str:
    """Format elapsed seconds as H:MM:SS or M:SS."""
    total = int(seconds)
    h, remainder = divmod(total, 3600)
    m, s = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _estimate_projection(
    tracer: Any,
    *,
    elapsed_seconds: float,
    current_cost: float,
    scan_completed: bool,
    scan_failed: bool,
) -> tuple[float, float, float]:
    """Estimate projected total duration/cost using live run-rate and agent progress."""
    if elapsed_seconds <= 0:
        return (0.0, 0.0, 0.0)

    if scan_completed or scan_failed:
        return (elapsed_seconds, current_cost, 0.0)

    statuses = [str(a.get("status", "running")).lower() for a in tracer.agents.values()]
    if not statuses:
        progress = 0.12
    else:
        status_weights = {
            "completed": 1.0,
            "stopped": 1.0,
            "failed": 1.0,
            "llm_failed": 1.0,
            "error": 1.0,
            "sandbox_failed": 1.0,
            "stopping": 0.85,
            "waiting": 0.55,
            "running": 0.35,
        }
        progress = sum(status_weights.get(status, 0.25) for status in statuses) / len(statuses)
        progress = max(0.08, min(0.98, progress))

    projected_total_duration = elapsed_seconds / progress
    projected_total_duration = max(elapsed_seconds, min(projected_total_duration, elapsed_seconds * 8))
    projected_cost = current_cost / progress if current_cost > 0 else 0.0
    projected_cost = max(current_cost, projected_cost)
    remaining_seconds = max(0.0, projected_total_duration - elapsed_seconds)
    return (projected_total_duration, projected_cost, remaining_seconds)


def build_tui_stats_text(
    tracer: Any,
    agent_config: dict[str, Any] | None = None,
    scan_completed: bool = False,
    scan_failed: bool = False,
    spinner_frame: int = 0,
    theme_tokens: dict[str, Any] | None = None,
) -> Text:
    stats_text = Text()
    if not tracer:
        return stats_text
    tokens = theme_tokens or get_theme_tokens(None)
    accent = str(tokens.get("accent", "#22d3ee"))
    info = str(tokens.get("info", "#60a5fa"))
    success = str(tokens.get("success", "#22c55e"))
    warning = str(tokens.get("warning", "#f59e0b"))
    error = str(tokens.get("error", "#ef4444"))
    muted = str(tokens.get("muted", "#9ca3af"))
    text_color = str(tokens.get("text", "white"))
    marker_colors = {marker: get_marker_color(tokens, marker) for marker in ("run", "ok", "warn", "err", "web")}

    model = ""
    # Model name with provider badge
    if agent_config:
        llm_config = agent_config["llm_config"]
        model = getattr(llm_config, "model_name", "Unknown")
        bare_model = model.split("/", 1)[-1] if "/" in model else model
        is_antigravity = model.startswith("antigravity/")

        # Provider badge
        if is_antigravity:
            stats_text.append("AG ", style="bold #a78bfa")
        elif "gpt" in bare_model.lower() or "o1" in bare_model or "o3" in bare_model or "o4" in bare_model or "codex" in bare_model.lower():
            stats_text.append("OAI ", style=f"bold {success}")
        elif "copilot" in model.lower():
            stats_text.append("CO ", style="bold #6366f1")
        elif "gemini" in bare_model.lower():
            stats_text.append("GG ", style="bold #4285f4")
        elif "claude" in bare_model.lower():
            stats_text.append("CC ", style="bold #d97706")
        stats_text.append(bare_model, style=text_color)

    # Elapsed time and scan status
    elapsed = 0.0
    elapsed_str = ""
    if hasattr(tracer, "start_time") and tracer.start_time:
        from datetime import datetime, timezone

        try:
            start = datetime.fromisoformat(tracer.start_time)
            # Use frozen end_time when scan is done/failed, otherwise live clock
            if (scan_completed or scan_failed) and hasattr(tracer, "end_time") and tracer.end_time:
                end = datetime.fromisoformat(tracer.end_time)
                elapsed = (end - start).total_seconds()
            else:
                elapsed = (datetime.now(timezone.utc) - start).total_seconds()
            elapsed_str = _format_elapsed(elapsed)
        except (ValueError, TypeError):
            pass

    if elapsed_str:
        stats_text.append("\n")
        if scan_failed:
            stats_text.append("[err] ", style=f"bold {marker_colors['err']}")
            stats_text.append(f"Failed  {elapsed_str}", style=error)
        elif scan_completed:
            stats_text.append("[ok] ", style=f"bold {marker_colors['ok']}")
            stats_text.append(f"Completed  {elapsed_str}", style=success)
        else:
            stats_text.append("[run] ", style=f"bold {marker_colors['run']}")
            stats_text.append(f"Scanning  {elapsed_str}", style=accent)

    # Divider
    stats_text.append("\n")
    stats_text.append("─" * 28, style="dim #3f3f3f")

    # Activity metrics
    agent_count = len(tracer.agents)
    tool_count = (
        tracer.get_real_tool_count()
        if hasattr(tracer, "get_real_tool_count")
        else len(tracer.tool_executions)
    )

    llm_stats = tracer.get_total_llm_stats()
    total_stats = llm_stats["total"]
    input_tokens = total_stats["input_tokens"]
    output_tokens = total_stats["output_tokens"]

    from esprit.llm.pricing import get_pricing_db, get_lifetime_cost
    cached_tokens = total_stats["cached_tokens"]
    uncached_input_tokens = int(
        llm_stats.get("uncached_input_tokens", max(0, input_tokens - cached_tokens))
    )
    cache_hit_ratio = float(
        llm_stats.get(
            "cache_hit_ratio",
            round((cached_tokens / max(input_tokens, 1)) * 100, 2) if input_tokens > 0 else 0.0,
        )
    )
    total_tokens = input_tokens + output_tokens
    requests = total_stats["requests"]
    context_tokens = llm_stats.get("max_context_tokens", 0)

    # Agents / Tools / Requests
    stats_text.append("\n")
    if scan_failed:
        activity_char = "[err]"
    elif scan_completed:
        activity_char = "[ok]"
    else:
        activity_char = "[run]"
    marker_key = "err" if scan_failed else "ok" if scan_completed else "run"
    stats_text.append(f"{activity_char} ", style=f"bold {marker_colors[marker_key]}")
    stats_text.append(f"{agent_count}", style=f"bold {text_color}")
    stats_text.append(" agents  ", style=f"dim {muted}")
    stats_text.append(f"{tool_count}", style=f"bold {text_color}")
    stats_text.append(" tools  ", style=f"dim {muted}")
    stats_text.append(f"{requests}", style=f"bold {text_color}")
    stats_text.append(" reqs", style=f"dim {muted}")

    # Token breakdown
    stats_text.append("\n")
    stats_text.append("─" * 28, style="dim #3f3f3f")

    if total_tokens > 0:
        stats_text.append("\n")
        stats_text.append("▸ In   ", style=f"dim {muted}")
        stats_text.append(f"{format_token_count(input_tokens):>6s}", style=text_color)
        if cached_tokens > 0:
            stats_text.append(f"  ({format_token_count(cached_tokens)} cached)", style="#a78bfa")

        stats_text.append("\n")
        stats_text.append("▸ Bill ", style=f"dim {muted}")
        stats_text.append(f"{format_token_count(uncached_input_tokens):>6s}", style=text_color)
        if input_tokens > 0:
            stats_text.append(f"  ({cache_hit_ratio:.0f}% hit)", style="#a78bfa")

        stats_text.append("\n")
        stats_text.append("▸ Out  ", style=f"dim {muted}")
        stats_text.append(f"{format_token_count(output_tokens):>6s}", style=text_color)

        # Tokens per second
        if elapsed > 0 and output_tokens > 0:
            tps = output_tokens / elapsed
            stats_text.append(f"  ({tps:.0f} tok/s)", style=f"dim {info}")

        stats_text.append("\n")
        stats_text.append("▸ Total ", style=f"dim {muted}")
        stats_text.append(f"{format_token_count(total_tokens):>5s}", style=f"bold {text_color}")

        # Context window usage bar — uses last request's input tokens (current window)
        context_limit = get_pricing_db().get_context_limit(model) if model else 128_000
        ctx_input = context_tokens if context_tokens > 0 else input_tokens
        ctx_pct = min(100, (ctx_input / max(context_limit, 1)) * 100)
        bar_width = 18
        filled = int(bar_width * ctx_pct / 100)
        bar_color = success if ctx_pct < 60 else warning if ctx_pct < 85 else error
        stats_text.append("\n")
        stats_text.append("▸ Ctx  ", style=f"dim {muted}")
        stats_text.append("█" * filled, style=bar_color)
        stats_text.append("░" * (bar_width - filled), style="dim #3f3f3f")
        stats_text.append(f" {ctx_pct:.0f}%", style=bar_color)
    else:
        stats_text.append("\n")
        stats_text.append("▸ Tokens ", style=f"dim {muted}")
        stats_text.append("0", style=f"dim {text_color}")

    # Cost — calculated from pricing DB
    stats_text.append("\n")
    stats_text.append("─" * 28, style="dim #3f3f3f")

    session_cost = get_pricing_db().get_cost(
        model, input_tokens, output_tokens, cached_tokens,
    ) if model else 0.0
    lifetime_cost = get_lifetime_cost()

    stats_text.append("\n")
    stats_text.append("  Cost ", style=f"dim {muted}")
    if session_cost >= 0.005:
        stats_text.append(f"${session_cost:.2f}", style=f"bold {warning}")
    else:
        stats_text.append(f"${session_cost:.2f}", style=f"dim {text_color}")
    if lifetime_cost >= 0.01:
        stats_text.append("  all-time ", style=f"dim {muted}")
        stats_text.append(f"${lifetime_cost:.2f}", style="dim #a78bfa")

    projected_duration, projected_cost, remaining_seconds = _estimate_projection(
        tracer,
        elapsed_seconds=elapsed,
        current_cost=session_cost,
        scan_completed=scan_completed,
        scan_failed=scan_failed,
    )
    if projected_duration > 0:
        stats_text.append("\n")
        stats_text.append("  Proj ", style=f"dim {muted}")
        projection_color = warning if projected_cost > (session_cost * 1.25) and session_cost > 0 else text_color
        stats_text.append(f"${projected_cost:.2f}", style=f"bold {projection_color}")
        stats_text.append("  total ", style=f"dim {muted}")
        stats_text.append(_format_elapsed(projected_duration), style=f"bold {projection_color}")
        if remaining_seconds > 0:
            stats_text.append("  left ", style=f"dim {muted}")
            stats_text.append(_format_elapsed(remaining_seconds), style=f"dim {projection_color}")

    # Vulnerabilities
    vuln_count = (
        len(tracer.vulnerability_reports)
        if hasattr(tracer, "vulnerability_reports")
        else 0
    )
    if vuln_count > 0:
        stats_text.append("\n")
        stats_text.append("─" * 28, style="dim #3f3f3f")
        stats_text.append("\n")
        stats_text.append("[warn] ", style=f"bold {marker_colors['warn']}")
        stats_text.append(f"{vuln_count} ", style=f"bold {error}")
        stats_text.append("vulns found", style=error)

        # Severity mini-breakdown
        severity_counts: dict[str, int] = {}
        for report in tracer.vulnerability_reports:
            sev = report.get("severity", "").lower()
            severity_counts[sev] = severity_counts.get(sev, 0) + 1
        sev_parts = []
        for sev in ["critical", "high", "medium", "low", "info"]:
            c = severity_counts.get(sev, 0)
            if c > 0:
                sev_parts.append((c, sev, get_severity_color(sev)))
        if sev_parts:
            stats_text.append("\n  ")
            for i, (c, label, color) in enumerate(sev_parts):
                if i > 0:
                    stats_text.append(" ", style="dim")
                stats_text.append(f"{c}{label[0].upper()}", style=f"bold {color}")

    # Rotating tips
    _TIPS = [
        ("[run]", "Send a message", "during a scan to interrupt and redirect the agent"),
        ("[run]", "Context at 100%?", "Esprit auto-compacts memory, summarizing older messages"),
        ("[info]", "esprit provider login", "to add OAuth accounts for free model access"),
        ("[info]", "esprit provider status", "to see which providers are connected"),
        ("[info]", "esprit config model", "to switch between AI models mid-session"),
        ("[warn]", "Press Esc", "to stop the current agent, Ctrl-Q to quit"),
        ("[info]", "Quick scan mode", "is faster but less thorough than deep scan"),
        ("[ok]", "Antigravity models", "are free — no API key or billing needed"),
        ("[info]", "Results are saved", "in esprit_runs/ after each scan completes"),
        ("[run]", "Add multiple accounts", "for OpenAI or Antigravity for rate-limit rotation"),
    ]
    tip_index = (spinner_frame // 30) % len(_TIPS)  # rotate every ~10 seconds
    marker, title, desc = _TIPS[tip_index]
    stats_text.append("\n")
    stats_text.append("─" * 28, style="dim #3f3f3f")
    stats_text.append("\n")
    marker_key = marker.strip("[]").lower()
    marker_color = marker_colors.get(marker_key, marker_colors["run"])
    stats_text.append(f"{marker} ", style=f"bold {marker_color}")
    stats_text.append(title, style=text_color)
    stats_text.append(f"\n  {desc}", style=f"dim {muted}")

    return stats_text


# Name generation utilities


def _slugify_for_run_name(text: str, max_length: int = 32) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")
    return text or "pentest"


def _derive_target_label_for_run_name(targets_info: list[dict[str, Any]] | None) -> str:  # noqa: PLR0911
    if not targets_info:
        return "pentest"

    first = targets_info[0]
    target_type = first.get("type")
    details = first.get("details", {}) or {}
    original = first.get("original", "") or ""

    if target_type == "web_application":
        url = details.get("target_url", original)
        try:
            parsed = urlparse(url)
            return str(parsed.netloc or parsed.path or url)
        except Exception:  # noqa: BLE001
            return str(url)

    if target_type == "repository":
        repo = details.get("target_repo", original)
        parsed = urlparse(repo)
        path = parsed.path or repo
        name = path.rstrip("/").split("/")[-1] or path
        if name.endswith(".git"):
            name = name[:-4]
        return str(name)

    if target_type == "local_code":
        path_str = details.get("target_path", original)
        try:
            return str(Path(path_str).name or path_str)
        except Exception:  # noqa: BLE001
            return str(path_str)

    if target_type == "ip_address":
        return str(details.get("target_ip", original) or original)

    return str(original or "pentest")


def generate_run_name(targets_info: list[dict[str, Any]] | None = None) -> str:
    base_label = _derive_target_label_for_run_name(targets_info)
    slug = _slugify_for_run_name(base_label)

    random_suffix = secrets.token_hex(2)

    return f"{slug}_{random_suffix}"


# Target processing utilities


def _is_http_git_repo(url: str) -> bool:
    check_url = f"{url.rstrip('/')}/info/refs?service=git-upload-pack"
    try:
        req = Request(check_url, headers={"User-Agent": "git/esprit"})  # noqa: S310
        with urlopen(req, timeout=10) as resp:  # noqa: S310  # nosec B310
            return "x-git-upload-pack-advertisement" in resp.headers.get("Content-Type", "")
    except HTTPError as e:
        return e.code == 401
    except (URLError, OSError, ValueError):
        return False


def infer_target_type(target: str) -> tuple[str, dict[str, str]]:  # noqa: PLR0911, PLR0912
    if not target or not isinstance(target, str):
        raise ValueError("Target must be a non-empty string")

    target = target.strip()

    if target.startswith("git@"):
        return "repository", {"target_repo": target}

    if target.startswith("git://"):
        return "repository", {"target_repo": target}

    parsed = urlparse(target)
    if parsed.scheme in ("http", "https"):
        if parsed.username or parsed.password:
            return "repository", {"target_repo": target}
        if parsed.path.rstrip("/").endswith(".git"):
            return "repository", {"target_repo": target}
        if parsed.query or parsed.fragment:
            return "web_application", {"target_url": target}
        path_segments = [s for s in parsed.path.split("/") if s]
        if len(path_segments) >= 2 and _is_http_git_repo(target):
            return "repository", {"target_repo": target}
        return "web_application", {"target_url": target}

    try:
        ip_obj = ipaddress.ip_address(target)
    except ValueError:
        pass
    else:
        return "ip_address", {"target_ip": str(ip_obj)}

    path = Path(target).expanduser()
    try:
        if path.exists():
            if path.is_dir():
                return "local_code", {"target_path": str(path.resolve())}
            raise ValueError(f"Path exists but is not a directory: {target}")
    except (OSError, RuntimeError) as e:
        raise ValueError(f"Invalid path: {target} - {e!s}") from e

    if target.endswith(".git"):
        return "repository", {"target_repo": target}

    if "/" in target:
        host_part, _, path_part = target.partition("/")
        if "." in host_part and not host_part.startswith(".") and path_part:
            full_url = f"https://{target}"
            if _is_http_git_repo(full_url):
                return "repository", {"target_repo": full_url}
            return "web_application", {"target_url": full_url}

    if "." in target and "/" not in target and not target.startswith("."):
        parts = target.split(".")
        if len(parts) >= 2 and all(p and p.strip() for p in parts):
            return "web_application", {"target_url": f"https://{target}"}

    raise ValueError(
        f"Invalid target: {target}\n"
        "Target must be one of:\n"
        "- A valid URL (http:// or https://)\n"
        "- A Git repository URL (https://host/org/repo or git@host:org/repo.git)\n"
        "- A local directory path\n"
        "- A domain name (e.g., example.com)\n"
        "- An IP address (e.g., 192.168.1.10)"
    )


def sanitize_name(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]", "-", name.strip())
    return sanitized or "target"


def derive_repo_base_name(repo_url: str) -> str:
    if repo_url.endswith("/"):
        repo_url = repo_url[:-1]

    if ":" in repo_url and repo_url.startswith("git@"):
        path_part = repo_url.split(":", 1)[1]
    else:
        path_part = urlparse(repo_url).path or repo_url

    candidate = path_part.split("/")[-1]
    if candidate.endswith(".git"):
        candidate = candidate[:-4]

    return sanitize_name(candidate or "repository")


def derive_local_base_name(path_str: str) -> str:
    try:
        base = Path(path_str).resolve().name
    except (OSError, RuntimeError):
        base = Path(path_str).name
    return sanitize_name(base or "workspace")


def assign_workspace_subdirs(targets_info: list[dict[str, Any]]) -> None:
    name_counts: dict[str, int] = {}

    for target in targets_info:
        target_type = target["type"]
        details = target["details"]

        base_name: str | None = None
        if target_type == "repository":
            base_name = derive_repo_base_name(details["target_repo"])
        elif target_type == "local_code":
            base_name = derive_local_base_name(details.get("target_path", "local"))

        if base_name is None:
            continue

        count = name_counts.get(base_name, 0) + 1
        name_counts[base_name] = count

        workspace_subdir = base_name if count == 1 else f"{base_name}-{count}"

        details["workspace_subdir"] = workspace_subdir


def collect_local_sources(targets_info: list[dict[str, Any]]) -> list[dict[str, str]]:
    local_sources: list[dict[str, str]] = []

    for target_info in targets_info:
        details = target_info["details"]
        workspace_subdir = details.get("workspace_subdir")

        if target_info["type"] == "local_code" and "target_path" in details:
            local_sources.append(
                {
                    "source_path": details["target_path"],
                    "workspace_subdir": workspace_subdir,
                }
            )

        elif target_info["type"] == "repository" and "cloned_repo_path" in details:
            local_sources.append(
                {
                    "source_path": details["cloned_repo_path"],
                    "workspace_subdir": workspace_subdir,
                }
            )

    return local_sources


def _is_localhost_host(host: str) -> bool:
    host_lower = host.lower().strip("[]")

    if host_lower in ("localhost", "0.0.0.0", "::1"):  # nosec B104
        return True

    try:
        ip = ipaddress.ip_address(host_lower)
        if isinstance(ip, ipaddress.IPv4Address):
            return ip.is_loopback  # 127.0.0.0/8
        if isinstance(ip, ipaddress.IPv6Address):
            return ip.is_loopback  # ::1
    except ValueError:
        pass

    return False


def rewrite_localhost_targets(targets_info: list[dict[str, Any]], host_gateway: str) -> None:
    from yarl import URL  # type: ignore[import-not-found]

    for target_info in targets_info:
        target_type = target_info.get("type")
        details = target_info.get("details", {})

        if target_type == "web_application":
            target_url = details.get("target_url", "")
            try:
                url = URL(target_url)
            except (ValueError, TypeError):
                continue

            if url.host and _is_localhost_host(url.host):
                details["target_url"] = str(url.with_host(host_gateway))

        elif target_type == "ip_address":
            target_ip = details.get("target_ip", "")
            if target_ip and _is_localhost_host(target_ip):
                details["target_ip"] = host_gateway


# Repository utilities
def clone_repository(repo_url: str, run_name: str, dest_name: str | None = None) -> str:
    console = Console()

    git_executable = shutil.which("git")
    if git_executable is None:
        raise FileNotFoundError("Git executable not found in PATH")

    temp_dir = Path(tempfile.gettempdir()) / "esprit_repos" / run_name
    temp_dir.mkdir(parents=True, exist_ok=True)

    if dest_name:
        repo_name = dest_name
    else:
        repo_name = Path(repo_url).stem if repo_url.endswith(".git") else Path(repo_url).name

    clone_path = temp_dir / repo_name

    if clone_path.exists():
        shutil.rmtree(clone_path)

    try:
        with console.status(f"[bold cyan]Cloning repository {repo_url}...", spinner="dots"):
            subprocess.run(  # noqa: S603
                [
                    git_executable,
                    "clone",
                    repo_url,
                    str(clone_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )

        return str(clone_path.absolute())

    except subprocess.CalledProcessError as e:
        error_text = Text()
        error_text.append("REPOSITORY CLONE FAILED", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append(f"Could not clone repository: {repo_url}\n", style="white")
        error_text.append(
            f"Error: {e.stderr if hasattr(e, 'stderr') and e.stderr else str(e)}", style="dim red"
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
    except FileNotFoundError:
        error_text = Text()
        error_text.append("GIT NOT FOUND", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append("Git is not installed or not available in PATH.\n", style="white")
        error_text.append("Please install Git to clone repositories.\n", style="white")

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


# Docker utilities
def check_docker_connection() -> Any:
    try:
        return docker.from_env()
    except DockerException:
        console = Console()
        error_text = Text()
        error_text.append("DOCKER NOT AVAILABLE", style="bold red")
        error_text.append("\n\n", style="white")
        error_text.append("Cannot connect to Docker daemon.\n", style="white")
        error_text.append(
            "Please ensure Docker Desktop is installed and running, and try running esprit again.\n",
            style="white",
        )

        panel = Panel(
            error_text,
            title="[bold white]ESPRIT",
            title_align="left",
            border_style="red",
            padding=(1, 2),
        )
        console.print("\n", panel, "\n")
        raise RuntimeError("Docker not available") from None


def image_exists(client: Any, image_name: str) -> bool:
    try:
        client.images.get(image_name)
    except ImageNotFound:
        return False
    else:
        return True


def update_layer_status(layers_info: dict[str, str], layer_id: str, layer_status: str) -> None:
    if "Pull complete" in layer_status or "Already exists" in layer_status:
        layers_info[layer_id] = "✓"
    elif "Downloading" in layer_status:
        layers_info[layer_id] = "↓"
    elif "Extracting" in layer_status:
        layers_info[layer_id] = "="
    elif "Waiting" in layer_status:
        layers_info[layer_id] = "."
    else:
        layers_info[layer_id] = "•"


def process_pull_line(
    line: dict[str, Any], layers_info: dict[str, str], status: Any, last_update: str
) -> str:
    if "id" in line and "status" in line:
        layer_id = line["id"]
        update_layer_status(layers_info, layer_id, line["status"])

        completed = sum(1 for v in layers_info.values() if v == "✓")
        total = len(layers_info)

        if total > 0:
            update_msg = f"[bold cyan]Progress: {completed}/{total} layers complete"
            if update_msg != last_update:
                status.update(update_msg)
                return update_msg

    elif "status" in line and "id" not in line:
        global_status = line["status"]
        if "Pulling from" in global_status:
            status.update("[bold cyan]Fetching image manifest...")
        elif "Digest:" in global_status:
            status.update("[bold cyan]Verifying image...")
        elif "Status:" in global_status:
            status.update("[bold cyan]Finalizing...")

    return last_update


# LLM utilities
def validate_llm_response(response: Any) -> None:
    if not response or not response.choices or not response.choices[0].message.content:
        raise RuntimeError("Invalid response from LLM")


def validate_config_file(config_path: str) -> Path:
    console = Console()
    path = Path(config_path)

    if not path.exists():
        console.print(f"[bold red]Error:[/] Config file not found: {config_path}")
        sys.exit(1)

    if path.suffix != ".json":
        console.print("[bold red]Error:[/] Config file must be a .json file")
        sys.exit(1)

    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        console.print(f"[bold red]Error:[/] Invalid JSON in config file: {e}")
        sys.exit(1)

    if not isinstance(data, dict):
        console.print("[bold red]Error:[/] Config file must contain a JSON object")
        sys.exit(1)

    if "env" not in data or not isinstance(data.get("env"), dict):
        console.print("[bold red]Error:[/] Config file must have an 'env' object")
        sys.exit(1)

    return path
