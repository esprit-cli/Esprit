from functools import cache
from typing import Any, ClassVar

from pygments.lexers import PythonLexer
from pygments.styles import get_style_by_name
from rich.padding import Padding
from rich.text import Text
from textual.widgets import Static

from esprit.interface.theme_tokens import get_theme_tokens_from_tool_data

from .base_renderer import BaseToolRenderer
from .registry import register_tool_renderer


@cache
def _get_style_colors() -> dict[Any, str]:
    style = get_style_by_name("native")
    return {token: f"#{style_def['color']}" for token, style_def in style if style_def["color"]}


BG_COLOR = "#141414"


@register_tool_renderer
class CreateVulnerabilityReportRenderer(BaseToolRenderer):
    tool_name: ClassVar[str] = "create_vulnerability_report"
    css_classes: ClassVar[list[str]] = ["tool-call", "reporting-tool"]

    SEVERITY_COLORS: ClassVar[dict[str, str]] = {
        "critical": "#dc2626",
        "high": "#ea580c",
        "medium": "#d97706",
        "low": "#65a30d",
        "info": "#0284c7",
    }

    @classmethod
    def _get_token_color(cls, token_type: Any) -> str | None:
        colors = _get_style_colors()
        while token_type:
            if token_type in colors:
                return colors[token_type]
            token_type = token_type.parent
        return None

    @classmethod
    def _highlight_python(cls, code: str) -> Text:
        lexer = PythonLexer()
        text = Text()

        for token_type, token_value in lexer.get_tokens(code):
            if not token_value:
                continue
            color = cls._get_token_color(token_type)
            text.append(token_value, style=color)

        return text

    @classmethod
    def _get_cvss_color(cls, cvss_score: float) -> str:
        if cvss_score >= 9.0:
            return "#dc2626"
        if cvss_score >= 7.0:
            return "#ea580c"
        if cvss_score >= 4.0:
            return "#d97706"
        if cvss_score >= 0.1:
            return "#65a30d"
        return "#6b7280"

    @classmethod
    def render(cls, tool_data: dict[str, Any]) -> Static:  # noqa: PLR0912, PLR0915
        args = tool_data.get("args", {})
        result = tool_data.get("result", {})
        tokens = get_theme_tokens_from_tool_data(tool_data)
        field_style = f"bold {str(tokens.get('success', '#4ade80'))}"
        muted_style = f"dim {str(tokens.get('muted', '#9ca3af'))}"
        header_style = str(tokens.get("warning", "#ea580c"))
        bug_style = str(tokens.get("error", "#dc2626"))

        title = args.get("title", "")
        description = args.get("description", "")
        impact = args.get("impact", "")
        target = args.get("target", "")
        technical_analysis = args.get("technical_analysis", "")
        poc_description = args.get("poc_description", "")
        poc_script_code = args.get("poc_script_code", "")
        remediation_steps = args.get("remediation_steps", "")

        attack_vector = args.get("attack_vector", "")
        attack_complexity = args.get("attack_complexity", "")
        privileges_required = args.get("privileges_required", "")
        user_interaction = args.get("user_interaction", "")
        scope = args.get("scope", "")
        confidentiality = args.get("confidentiality", "")
        integrity = args.get("integrity", "")
        availability = args.get("availability", "")

        endpoint = args.get("endpoint", "")
        method = args.get("method", "")
        cve = args.get("cve", "")

        severity = ""
        cvss_score = None
        if isinstance(result, dict):
            severity = result.get("severity", "")
            cvss_score = result.get("cvss_score")

        text = Text()
        text.append("[bug] ", style=f"bold {bug_style}")
        text.append("Vulnerability Report", style=f"bold {header_style}")

        if title:
            text.append("\n\n")
            text.append("Title: ", style=field_style)
            text.append(title)

        if severity:
            text.append("\n\n")
            text.append("Severity: ", style=field_style)
            severity_color = cls.SEVERITY_COLORS.get(severity.lower(), "#6b7280")
            text.append(severity.upper(), style=f"bold {severity_color}")

        if cvss_score is not None:
            text.append("\n\n")
            text.append("CVSS Score: ", style=field_style)
            cvss_color = cls._get_cvss_color(cvss_score)
            text.append(str(cvss_score), style=f"bold {cvss_color}")

        if target:
            text.append("\n\n")
            text.append("Target: ", style=field_style)
            text.append(target)

        if endpoint:
            text.append("\n\n")
            text.append("Endpoint: ", style=field_style)
            text.append(endpoint)

        if method:
            text.append("\n\n")
            text.append("Method: ", style=field_style)
            text.append(method)

        if cve:
            text.append("\n\n")
            text.append("CVE: ", style=field_style)
            text.append(cve)

        if any(
            [
                attack_vector,
                attack_complexity,
                privileges_required,
                user_interaction,
                scope,
                confidentiality,
                integrity,
                availability,
            ]
        ):
            text.append("\n\n")
            cvss_parts = []
            if attack_vector:
                cvss_parts.append(f"AV:{attack_vector}")
            if attack_complexity:
                cvss_parts.append(f"AC:{attack_complexity}")
            if privileges_required:
                cvss_parts.append(f"PR:{privileges_required}")
            if user_interaction:
                cvss_parts.append(f"UI:{user_interaction}")
            if scope:
                cvss_parts.append(f"S:{scope}")
            if confidentiality:
                cvss_parts.append(f"C:{confidentiality}")
            if integrity:
                cvss_parts.append(f"I:{integrity}")
            if availability:
                cvss_parts.append(f"A:{availability}")
            text.append("CVSS Vector: ", style=field_style)
            text.append("/".join(cvss_parts), style=muted_style)

        if description:
            text.append("\n\n")
            text.append("Description", style=field_style)
            text.append("\n")
            text.append(description)

        if impact:
            text.append("\n\n")
            text.append("Impact", style=field_style)
            text.append("\n")
            text.append(impact)

        if technical_analysis:
            text.append("\n\n")
            text.append("Technical Analysis", style=field_style)
            text.append("\n")
            text.append(technical_analysis)

        if poc_description:
            text.append("\n\n")
            text.append("PoC Description", style=field_style)
            text.append("\n")
            text.append(poc_description)

        if poc_script_code:
            text.append("\n\n")
            text.append("PoC Code", style=field_style)
            text.append("\n")
            text.append_text(cls._highlight_python(poc_script_code))

        if remediation_steps:
            text.append("\n\n")
            text.append("Remediation", style=field_style)
            text.append("\n")
            text.append(remediation_steps)

        if not title:
            text.append("\n  ")
            text.append("Creating report...", style=muted_style)

        padded = Padding(text, 2, style=f"on {BG_COLOR}")

        css_classes = cls.get_css_classes("completed")
        return Static(padded, classes=css_classes)
