import argparse
import asyncio
import atexit
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import TYPE_CHECKING, Any, ClassVar


if TYPE_CHECKING:
    from textual.timer import Timer

    from esprit.interface.updater import UpdateInfo

from rich.align import Align
from rich.console import Group
from rich.panel import Panel
from rich.style import Style
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Horizontal, Vertical, VerticalScroll
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static, TextArea, Tree
from textual.widgets.tree import TreeNode

from esprit.agents.EspritAgent import EspritAgent
from esprit.config import Config
# IMPORTANT: import image_protocol BEFORE the Textual app starts so that
# textual-image can query the terminal for Kitty/Sixel support while stdout
# is still a real TTY.
import esprit.interface.image_protocol as _image_proto  # noqa: F401
from esprit.interface.streaming_parser import parse_streaming_content
from esprit.interface.tool_components.agent_message_renderer import AgentMessageRenderer
from esprit.interface.tool_components.registry import get_tool_renderer
from esprit.interface.tool_components.user_message_renderer import UserMessageRenderer
from esprit.interface.theme_tokens import (
    DEFAULT_THEME_ID,
    SUPPORTED_THEME_IDS,
    get_marker_color,
    get_theme_tokens,
    normalize_theme_id,
)
from esprit.interface.utils import (
    build_tui_stats_text,
    format_token_count,
    format_vulnerability_report,
)
from esprit.llm.config import LLMConfig
from esprit.telemetry.tracer import Tracer, set_global_tracer


# Type alias for the optional GUI server
_GUIServerType = Any


def get_package_version() -> str:
    try:
        return pkg_version("esprit-agent")
    except PackageNotFoundError:
        return "dev"


class ChatTextArea(TextArea):  # type: ignore[misc]
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._app_reference: EspritTUIApp | None = None

    def set_app_reference(self, app: "EspritTUIApp") -> None:
        self._app_reference = app

    def on_mount(self) -> None:
        self._update_height()

    def _on_key(self, event: events.Key) -> None:
        if self._app_reference:
            key = str(event.key or "")
            if key == "ctrl+v":
                self._app_reference.action_toggle_vulnerability_overlay()
                event.prevent_default()
                event.stop()
                return
            if key == "ctrl+h":
                self._app_reference.action_toggle_agent_health_popup()
                event.prevent_default()
                event.stop()
                return

        if event.key == "shift+enter":
            self.insert("\n")
            event.prevent_default()
            return

        if event.key == "enter" and self._app_reference:
            text_content = str(self.text)  # type: ignore[has-type]
            message = text_content.strip()
            if message:
                self.text = ""

                self._app_reference._send_user_message(message)

                event.prevent_default()
                return

        super()._on_key(event)

    @on(TextArea.Changed)  # type: ignore[misc]
    def _update_height(self, _event: TextArea.Changed | None = None) -> None:
        if not self.parent:
            return

        line_count = self.document.line_count
        target_lines = min(max(1, line_count), 6)

        new_height = max(3, target_lines + 2)

        if self.parent.styles.height != new_height:
            self.parent.styles.height = new_height
            self.scroll_cursor_visible()


class SplashScreen(Static):  # type: ignore[misc]
    WORDMARK = (
        "███████ ███████ ██████  ██████  ██ ████████",
        "██      ██      ██   ██ ██   ██ ██    ██",
        "█████   ███████ ██████  ██████  ██    ██",
        "██           ██ ██      ██   ██ ██    ██",
        "███████ ███████ ██      ██   ██ ██    ██",
    )
    GHOST_FRAMES: ClassVar[list[tuple[str, ...]]] = [
        (
            "        *               *       ",
            "         [][][][][][][]         ",
            "       [][][][][][][][][]       ",
            "      [][][][][][][][][][][]    ",
            "      [][]  [][][][][]  [][]    ",
            "      [][][][][][][][][][][]    ",
            "      [][][][][][][][][][][]    ",
            "      [][][][][][][][][][][]    ",
            "      [][]  [][][][][]  [][]    ",
            "        [][][][][][][][][]      ",
            "        [][]  [][]  [][]        ",
            "      [][]          [][]        ",
        ),
        (
            "      *               *         ",
            "          [][][][][][][]        ",
            "        [][][][][][][][][]      ",
            "      [][][][][][][][][][][]    ",
            "      [][]   [][][][]   [][]    ",
            "      [][][][][][][][][][][]    ",
            "      [][][][][][][][][][][]    ",
            "      [][][][][][][][][][][]    ",
            "      [][]   [][][][]   [][]    ",
            "        [][][][][][][][][]      ",
            "       [][]  [][]  [][]         ",
            "       [][]          [][]       ",
        ),
        (
            "         *               *      ",
            "        [][][][][][][]          ",
            "      [][][][][][][][][]        ",
            "    [][][][][][][][][][][]      ",
            "    [][]  [][][][][]  [][]      ",
            "    [][][][][][][][][][][]      ",
            "    [][][][][][][][][][][]      ",
            "    [][][][][][][][][][][]      ",
            "    [][]  [][][][][]  [][]      ",
            "      [][][][][][][][][]        ",
            "       [][]  [][]  [][]         ",
            "     [][]          [][]         ",
        ),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._animation_step = 0
        self._animation_timer: Timer | None = None
        self._panel_static: Static | None = None
        self._version = "dev"
        self._theme_id = normalize_theme_id(Config.get_launchpad_theme())

    def _theme_tokens(self) -> dict[str, Any]:
        return get_theme_tokens(self._theme_id)

    def compose(self) -> ComposeResult:
        self._version = get_package_version()
        self._animation_step = 0
        start_line = self._build_start_line_text(self._animation_step)
        panel = self._build_panel(start_line)

        panel_static = Static(panel, id="splash_content")
        self._panel_static = panel_static
        yield panel_static

    def on_mount(self) -> None:
        self._animation_timer = self.set_interval(0.12, self._animate_start_line)

    def on_unmount(self) -> None:
        if self._animation_timer is not None:
            self._animation_timer.stop()
            self._animation_timer = None

    def _animate_start_line(self) -> None:
        if not self._panel_static:
            return

        self._animation_step += 1
        start_line = self._build_start_line_text(self._animation_step)
        panel = self._build_panel(start_line)
        self._panel_static.update(panel)

    def _build_panel(self, start_line: Text) -> Panel:
        tokens = self._theme_tokens()
        content = Group(
            Align.center(self._build_ghost_text(self._animation_step)),
            Align.center(Text(" ")),
            Align.center(self._build_wordmark_text(self._animation_step)),
            Align.center(Text(" ")),
            Align.center(self._build_welcome_text()),
            Align.center(self._build_version_text()),
            Align.center(self._build_tagline_text()),
            Align.center(Text(" ")),
            Align.center(start_line.copy()),
        )

        return Panel.fit(content, border_style=str(tokens.get("accent", "#22d3ee")), padding=(1, 4))

    def _build_welcome_text(self) -> Text:
        tokens = self._theme_tokens()
        text = Text("Ghost shell online: ", style=Style(color=str(tokens.get("text", "white")), bold=True))
        text.append("Esprit", style=Style(color=str(tokens.get("accent", "#22d3ee")), bold=True))
        return text

    def _build_version_text(self) -> Text:
        tokens = self._theme_tokens()
        return Text(f"v{self._version}", style=Style(color=str(tokens.get("muted", "#9ca3af")), dim=True))

    def _build_tagline_text(self) -> Text:
        tokens = self._theme_tokens()
        return Text(
            "Open-source AI hackers for your apps",
            style=Style(color=str(tokens.get("muted", "#9ca3af")), dim=True),
        )

    def _build_wordmark_text(self, phase: int) -> Text:
        tokens = self._theme_tokens()
        palette = (
            str(tokens.get("info", "#7dd3fc")),
            str(tokens.get("accent", "#38bdf8")),
            str(tokens.get("info", "#22d3ee")),
            str(tokens.get("accent", "#06b6d4")),
            str(tokens.get("muted", "#0891b2")),
        )
        highlight = str(tokens.get("text", "#ecfeff"))
        bright = str(tokens.get("info", "#bae6fd"))
        sweep = (phase * 2) % 56
        wordmark = Text(justify="center")

        for row_index, row in enumerate(self.WORDMARK):
            base = palette[(row_index + phase // 4) % len(palette)]
            row_text = Text()
            for col_index, char in enumerate(row):
                if char == " ":
                    row_text.append(char)
                    continue

                dist = abs(col_index - sweep)
                if dist <= 1:
                    style = Style(color=highlight, bold=True)
                elif dist <= 3:
                    style = Style(color=bright, bold=True)
                else:
                    style = Style(color=base, bold=True)
                row_text.append(char, style=style)

            wordmark.append_text(row_text)
            if row_index < len(self.WORDMARK) - 1:
                wordmark.append("\n")
        return wordmark

    def _build_ghost_text(self, phase: int) -> Text:
        tokens = self._theme_tokens()
        body_color = str(tokens.get("accent", "#22d3ee"))
        sparkle_a = str(tokens.get("info", "#a5f3fc"))
        sparkle_b = str(tokens.get("accent", "#38bdf8"))
        frame = self.GHOST_FRAMES[phase % len(self.GHOST_FRAMES)]
        ghost = Text()

        for line_index, line in enumerate(frame):
            line_text = Text()
            i = 0
            while i < len(line):
                chunk = line[i : i + 2]
                if chunk == "[]":
                    line_text.append("██", style=Style(color=body_color, bold=True))
                    i += 2
                    continue

                char = line[i]
                if char == "*":
                    sparkle = sparkle_a if (phase + line_index + i) % 2 == 0 else sparkle_b
                    line_text.append(char, style=Style(color=sparkle, bold=True))
                else:
                    line_text.append(char)
                i += 1

            ghost.append_text(line_text)
            if line_index < len(frame) - 1:
                ghost.append("\n")

        return ghost

    def _build_start_line_text(self, phase: int) -> Text:
        tokens = self._theme_tokens()
        text_color = str(tokens.get("text", "#f5f5f5"))
        info_color = str(tokens.get("info", "#d4d4d8"))
        muted_color = str(tokens.get("muted", "#a3a3a3"))
        full_text = "Booting ghost runtime"
        text_len = len(full_text)

        shine_pos = phase % (text_len + 8)

        text = Text()
        for i, char in enumerate(full_text):
            dist = abs(i - shine_pos)

            if dist <= 1:
                style = Style(color=text_color, bold=True)
            elif dist <= 3:
                style = Style(color=info_color, bold=True)
            elif dist <= 5:
                style = Style(color=muted_color)
            else:
                style = Style(color=muted_color, dim=True)

            text.append(char, style=style)

        return text


class HelpScreen(ModalScreen):  # type: ignore[misc]
    def compose(self) -> ComposeResult:
        yield Grid(
            Label("Esprit Help", id="help_title"),
            Label(
                "F1        Help\nCtrl+Q/C  Quit\nESC       Stop Agent\n"
                "Enter     Send message to agent\nTab       Switch panels\n↑/↓       Navigate tree\n"
                "b         Browser preview\n"
                "Ctrl+V    Vulnerability overlay\n"
                "Ctrl+H    Agent health popup\n"
                "Ctrl+U    Check for updates",
                id="help_content",
            ),
            id="dialog",
        )

    def on_key(self, _event: events.Key) -> None:
        self.app.pop_screen()


class HelpOverlay(ModalScreen):  # type: ignore[misc]
    """Modal overlay listing all keyboard shortcuts."""

    def compose(self) -> ComposeResult:
        help_text = Text()
        help_text.append("  Keyboard Shortcuts\n", style="bold underline")
        help_text.append("\n")
        help_text.append("  Navigation              Actions\n", style="bold")
        help_text.append("  ─────────              ───────\n", style="dim")
        help_text.append("  Ctrl+V  ", style="bold cyan")
        help_text.append("Vulnerabilities    ")
        help_text.append("Ctrl+L  ", style="bold cyan")
        help_text.append("Clear chat\n")
        help_text.append("  Ctrl+H  ", style="bold cyan")
        help_text.append("Agent health       ")
        help_text.append("Ctrl+U  ", style="bold cyan")
        help_text.append("Check updates\n")
        help_text.append("  Ctrl+Q  ", style="bold cyan")
        help_text.append("Quit               ")
        help_text.append("Escape  ", style="bold cyan")
        help_text.append("Stop agent\n")
        help_text.append("  B       ", style="bold cyan")
        help_text.append("Browser preview    ")
        help_text.append("?       ", style="bold cyan")
        help_text.append("This help\n")
        help_text.append("  F1      ", style="bold cyan")
        help_text.append("Help bar           ")
        help_text.append("Enter   ", style="bold cyan")
        help_text.append("Send message\n")
        help_text.append("                           ")
        help_text.append("Shift+Enter  ", style="bold cyan")
        help_text.append("New line\n")
        help_text.append("\n")
        help_text.append("  Press Escape or ? to close", style="dim italic")

        panel = Panel(
            Align.center(help_text),
            title="Esprit Help",
            border_style="cyan",
            padding=(1, 2),
        )
        yield Static(Align.center(panel), id="help_overlay_content")

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "question_mark"):
            self.app.pop_screen()
            event.prevent_default()


class StopAgentScreen(ModalScreen):  # type: ignore[misc]
    def __init__(self, agent_name: str, agent_id: str):
        super().__init__()
        self.agent_name = agent_name
        self.agent_id = agent_id

    def compose(self) -> ComposeResult:
        theme_tokens = get_theme_tokens(Config.get_launchpad_theme())
        title = Text()
        title.append("[warn] ", style=f"bold {get_marker_color(theme_tokens, 'warn')}")
        title.append(f"Stop '{self.agent_name}'?")
        yield Grid(
            Label(title, id="stop_agent_title"),
            Grid(
                Button("Yes", variant="error", id="stop_agent"),
                Button("No", variant="default", id="cancel_stop"),
                id="stop_agent_buttons",
            ),
            id="stop_agent_dialog",
        )

    def on_mount(self) -> None:
        stop_button = self.query_one("#stop_agent", Button)
        stop_button.focus()

    def on_key(self, event: events.Key) -> None:
        if event.key in ("left", "right", "up", "down"):
            focused = self.focused

            if focused and focused.id == "stop_agent":
                cancel_button = self.query_one("#cancel_stop", Button)
                cancel_button.focus()
            else:
                stop_button = self.query_one("#stop_agent", Button)
                stop_button.focus()

            event.prevent_default()
        elif event.key == "enter":
            focused = self.focused
            if focused and isinstance(focused, Button):
                focused.press()
            event.prevent_default()
        elif event.key == "escape":
            self.app.pop_screen()
            event.prevent_default()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "stop_agent":
            self.app.action_confirm_stop_agent(self.agent_id)
        else:
            self.app.pop_screen()


class VulnerabilityDetailScreen(ModalScreen):  # type: ignore[misc]
    """Modal screen to display vulnerability details."""

    SEVERITY_COLORS: ClassVar[dict[str, str]] = {
        "critical": "#dc2626",  # Red
        "high": "#ea580c",  # Orange
        "medium": "#d97706",  # Amber
        "low": "#22c55e",  # Green
        "info": "#3b82f6",  # Blue
    }

    FIELD_STYLE: ClassVar[str] = "bold #4ade80"

    def __init__(self, vulnerability: dict[str, Any]) -> None:
        super().__init__()
        self.vulnerability = vulnerability

    def compose(self) -> ComposeResult:
        content = self._render_vulnerability()
        yield Grid(
            VerticalScroll(Static(content, id="vuln_detail_content"), id="vuln_detail_scroll"),
            Horizontal(
                Button("Copy", variant="default", id="copy_vuln_detail"),
                Button("Done", variant="default", id="close_vuln_detail"),
                id="vuln_detail_buttons",
            ),
            id="vuln_detail_dialog",
        )

    def on_mount(self) -> None:
        close_button = self.query_one("#close_vuln_detail", Button)
        close_button.focus()

    def _get_cvss_color(self, cvss_score: float) -> str:
        if cvss_score >= 9.0:
            return "#dc2626"
        if cvss_score >= 7.0:
            return "#ea580c"
        if cvss_score >= 4.0:
            return "#d97706"
        if cvss_score >= 0.1:
            return "#65a30d"
        return "#6b7280"

    def _highlight_python(self, code: str) -> Text:
        try:
            from pygments.lexers import PythonLexer
            from pygments.styles import get_style_by_name

            lexer = PythonLexer()
            style = get_style_by_name("native")
            colors = {
                token: f"#{style_def['color']}" for token, style_def in style if style_def["color"]
            }

            text = Text()
            for token_type, token_value in lexer.get_tokens(code):
                if not token_value:
                    continue
                color = None
                tt = token_type
                while tt:
                    if tt in colors:
                        color = colors[tt]
                        break
                    tt = tt.parent
                text.append(token_value, style=color)
        except (ImportError, KeyError, AttributeError):
            return Text(code)
        else:
            return text

    def _render_vulnerability(self) -> Text:  # noqa: PLR0912, PLR0915
        vuln = self.vulnerability
        theme_tokens = get_theme_tokens(Config.get_launchpad_theme())
        text = Text()

        text.append(
            "[bug] ", style=f"bold {get_marker_color(theme_tokens, 'bug')}"
        )
        text.append("Vulnerability Report", style="bold #ea580c")

        agent_name = vuln.get("agent_name", "")
        if agent_name:
            text.append("\n\n")
            text.append("Agent: ", style=self.FIELD_STYLE)
            text.append(agent_name)

        title = vuln.get("title", "")
        if title:
            text.append("\n\n")
            text.append("Title: ", style=self.FIELD_STYLE)
            text.append(title)

        severity = vuln.get("severity", "")
        if severity:
            text.append("\n\n")
            text.append("Severity: ", style=self.FIELD_STYLE)
            severity_color = self.SEVERITY_COLORS.get(severity.lower(), "#6b7280")
            text.append(severity.upper(), style=f"bold {severity_color}")

        cvss_score = vuln.get("cvss")
        if cvss_score is not None:
            text.append("\n\n")
            text.append("CVSS Score: ", style=self.FIELD_STYLE)
            cvss_color = self._get_cvss_color(float(cvss_score))
            text.append(str(cvss_score), style=f"bold {cvss_color}")

        target = vuln.get("target", "")
        if target:
            text.append("\n\n")
            text.append("Target: ", style=self.FIELD_STYLE)
            text.append(target)

        endpoint = vuln.get("endpoint", "")
        if endpoint:
            text.append("\n\n")
            text.append("Endpoint: ", style=self.FIELD_STYLE)
            text.append(endpoint)

        method = vuln.get("method", "")
        if method:
            text.append("\n\n")
            text.append("Method: ", style=self.FIELD_STYLE)
            text.append(method)

        cve = vuln.get("cve", "")
        if cve:
            text.append("\n\n")
            text.append("CVE: ", style=self.FIELD_STYLE)
            text.append(cve)

        # CVSS breakdown
        cvss_breakdown = vuln.get("cvss_breakdown", {})
        if cvss_breakdown:
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
                text.append("\n\n")
                text.append("CVSS Vector: ", style=self.FIELD_STYLE)
                text.append("/".join(cvss_parts), style="dim")

        description = vuln.get("description", "")
        if description:
            text.append("\n\n")
            text.append("Description", style=self.FIELD_STYLE)
            text.append("\n")
            text.append(description)

        impact = vuln.get("impact", "")
        if impact:
            text.append("\n\n")
            text.append("Impact", style=self.FIELD_STYLE)
            text.append("\n")
            text.append(impact)

        technical_analysis = vuln.get("technical_analysis", "")
        if technical_analysis:
            text.append("\n\n")
            text.append("Technical Analysis", style=self.FIELD_STYLE)
            text.append("\n")
            text.append(technical_analysis)

        poc_description = vuln.get("poc_description", "")
        if poc_description:
            text.append("\n\n")
            text.append("PoC Description", style=self.FIELD_STYLE)
            text.append("\n")
            text.append(poc_description)

        poc_script_code = vuln.get("poc_script_code", "")
        if poc_script_code:
            text.append("\n\n")
            text.append("PoC Code", style=self.FIELD_STYLE)
            text.append("\n")
            text.append_text(self._highlight_python(poc_script_code))

        remediation_steps = vuln.get("remediation_steps", "")
        if remediation_steps:
            text.append("\n\n")
            text.append("Remediation", style=self.FIELD_STYLE)
            text.append("\n")
            text.append(remediation_steps)

        return text

    def _get_markdown_report(self) -> str:  # noqa: PLR0912, PLR0915
        """Get Markdown version of vulnerability report for clipboard."""
        vuln = self.vulnerability
        lines: list[str] = []

        # Title
        title = vuln.get("title", "Untitled Vulnerability")
        lines.append(f"# {title}")
        lines.append("")

        # Metadata
        if vuln.get("id"):
            lines.append(f"**ID:** {vuln['id']}")
        if vuln.get("severity"):
            lines.append(f"**Severity:** {vuln['severity'].upper()}")
        if vuln.get("timestamp"):
            lines.append(f"**Found:** {vuln['timestamp']}")
        if vuln.get("agent_name"):
            lines.append(f"**Agent:** {vuln['agent_name']}")
        if vuln.get("target"):
            lines.append(f"**Target:** {vuln['target']}")
        if vuln.get("endpoint"):
            lines.append(f"**Endpoint:** {vuln['endpoint']}")
        if vuln.get("method"):
            lines.append(f"**Method:** {vuln['method']}")
        if vuln.get("cve"):
            lines.append(f"**CVE:** {vuln['cve']}")
        if vuln.get("cvss") is not None:
            lines.append(f"**CVSS:** {vuln['cvss']}")

        # CVSS Vector
        cvss_breakdown = vuln.get("cvss_breakdown", {})
        if cvss_breakdown:
            abbrevs = {
                "attack_vector": "AV",
                "attack_complexity": "AC",
                "privileges_required": "PR",
                "user_interaction": "UI",
                "scope": "S",
                "confidentiality": "C",
                "integrity": "I",
                "availability": "A",
            }
            parts = [
                f"{abbrevs.get(k, k)}:{v}" for k, v in cvss_breakdown.items() if v and k in abbrevs
            ]
            if parts:
                lines.append(f"**CVSS Vector:** {'/'.join(parts)}")

        # Description
        lines.append("")
        lines.append("## Description")
        lines.append("")
        lines.append(vuln.get("description") or "No description provided.")

        # Impact
        if vuln.get("impact"):
            lines.extend(["", "## Impact", "", vuln["impact"]])

        # Technical Analysis
        if vuln.get("technical_analysis"):
            lines.extend(["", "## Technical Analysis", "", vuln["technical_analysis"]])

        # Proof of Concept
        if vuln.get("poc_description") or vuln.get("poc_script_code"):
            lines.extend(["", "## Proof of Concept", ""])
            if vuln.get("poc_description"):
                lines.append(vuln["poc_description"])
                lines.append("")
            if vuln.get("poc_script_code"):
                lines.append("```python")
                lines.append(vuln["poc_script_code"])
                lines.append("```")

        # Code Analysis
        if vuln.get("code_file") or vuln.get("code_diff"):
            lines.extend(["", "## Code Analysis", ""])
            if vuln.get("code_file"):
                lines.append(f"**File:** {vuln['code_file']}")
                lines.append("")
            if vuln.get("code_diff"):
                lines.append("**Changes:**")
                lines.append("```diff")
                lines.append(vuln["code_diff"])
                lines.append("```")

        # Remediation
        if vuln.get("remediation_steps"):
            lines.extend(["", "## Remediation", "", vuln["remediation_steps"]])

        lines.append("")
        return "\n".join(lines)

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.app.pop_screen()
            event.prevent_default()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy_vuln_detail":
            markdown_text = self._get_markdown_report()
            self.app.copy_to_clipboard(markdown_text)

            copy_button = self.query_one("#copy_vuln_detail", Button)
            copy_button.label = "Copied!"
            copy_button.variant = "success"
            self.set_timer(2.5, lambda: (setattr(copy_button, "label", "Copy"), setattr(copy_button, "variant", "default")))
        elif event.button.id == "close_vuln_detail":
            self.app.pop_screen()


class BrowserPreviewScreen(ModalScreen):  # type: ignore[misc]
    """Modal screen showing an enlarged browser screenshot preview with auto-refresh."""

    def __init__(self, screenshot_b64: str, url: str = "", agent_id: str = "") -> None:
        super().__init__()
        self._screenshot_b64 = screenshot_b64
        self._url = url
        self._agent_id = agent_id
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        from esprit.interface.image_widget import BrowserScreenshotWidget

        yield Grid(
            VerticalScroll(
                BrowserScreenshotWidget(
                    screenshot_b64=self._screenshot_b64,
                    url=self._url,
                    id="browser_preview_widget",
                ),
                id="browser_preview_scroll",
            ),
            Horizontal(
                Button("Close", variant="default", id="close_browser_preview"),
                id="browser_preview_buttons",
            ),
            id="browser_preview_dialog",
        )

    def on_mount(self) -> None:
        close_button = self.query_one("#close_browser_preview", Button)
        close_button.focus()
        # Start auto-refresh if we have an agent_id
        if self._agent_id:
            self._refresh_timer = self.set_interval(1.0, self._check_for_new_screenshot)

    def _check_for_new_screenshot(self) -> None:
        """Poll for new screenshots and update the widget if changed."""
        if not self._agent_id:
            return
        try:
            app = self.app
            if not isinstance(app, EspritTUIApp):
                return
            new_b64, new_url = app._get_latest_browser_screenshot(self._agent_id)
            if new_b64 and new_b64 != self._screenshot_b64:
                self._screenshot_b64 = new_b64
                self._url = new_url
                try:
                    from esprit.interface.image_widget import BrowserScreenshotWidget

                    widget = self.query_one("#browser_preview_widget", BrowserScreenshotWidget)
                    widget.update_screenshot(new_b64, new_url)
                except (ValueError, Exception):
                    pass
        except Exception:  # noqa: BLE001
            pass

    def _render_preview(self) -> Text:
        try:
            from esprit.interface.image_renderer import screenshot_to_rich_text

            result = screenshot_to_rich_text(
                self._screenshot_b64, max_width=0, url_label=self._url
            )
            if result is not None:
                return result
        except Exception:
            logging.debug("Browser preview render failed", exc_info=True)
        text = Text()
        text.append("Unable to render browser preview", style="dim")
        return text

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "b"):
            if self._refresh_timer:
                self._refresh_timer.stop()
            self.app.pop_screen()
            event.prevent_default()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close_browser_preview":
            if self._refresh_timer:
                self._refresh_timer.stop()
            self.app.pop_screen()


class VulnerabilityItem(Static):  # type: ignore[misc]
    """A clickable vulnerability item."""

    def __init__(self, label: Text, vuln_data: dict[str, Any], **kwargs: Any) -> None:
        super().__init__(label, **kwargs)
        self.vuln_data = vuln_data

    def on_click(self, _event: events.Click) -> None:
        """Handle click to open vulnerability detail."""
        self.app.push_screen(VulnerabilityDetailScreen(self.vuln_data))


class VulnerabilitiesPanel(VerticalScroll):  # type: ignore[misc]
    """A scrollable panel showing found vulnerabilities with severity-colored dots."""

    SEVERITY_COLORS: ClassVar[dict[str, str]] = {
        "critical": "#dc2626",  # Red
        "high": "#ea580c",  # Orange
        "medium": "#d97706",  # Amber
        "low": "#22c55e",  # Green
        "info": "#3b82f6",  # Blue
    }

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._vulnerabilities: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        yield Static(
            "No vulnerabilities yet.\nFindings will appear here in realtime.",
            classes="vuln-empty",
        )

    def update_vulnerabilities(self, vulnerabilities: list[dict[str, Any]]) -> None:
        """Update the list of vulnerabilities and re-render."""
        if self._vulnerabilities == vulnerabilities:
            return
        self._vulnerabilities = list(vulnerabilities)
        self._render_panel()

    def _render_panel(self) -> None:
        """Render the vulnerabilities panel content."""
        for child in list(self.children):
            child.remove()

        if not self._vulnerabilities:
            self.mount(
                Static(
                    "No vulnerabilities yet.\nFindings will appear here in realtime.",
                    classes="vuln-empty",
                )
            )
            return

        for vuln in self._vulnerabilities:
            severity = vuln.get("severity", "info").lower()
            title = vuln.get("title", "Unknown Vulnerability")
            color = self.SEVERITY_COLORS.get(severity, "#3b82f6")

            label = Text()
            label.append("● ", style=Style(color=color))
            label.append(title, style=Style(color="#d4d4d4"))

            item = VulnerabilityItem(label, vuln, classes="vuln-item")
            self.mount(item)


class VulnerabilityOverlayScreen(ModalScreen):  # type: ignore[misc]
    """Dedicated vulnerability workspace with list, detail, and copy actions."""

    SEVERITY_COLORS: ClassVar[dict[str, str]] = {
        "critical": "#dc2626",
        "high": "#ea580c",
        "medium": "#d97706",
        "low": "#65a30d",
        "info": "#0284c7",
    }

    _SEVERITY_CYCLE: ClassVar[list[str]] = [
        "all", "critical", "high", "medium", "low", "info",
    ]

    def __init__(self) -> None:
        super().__init__()
        self._selected_index = 0
        self._refresh_timer: Timer | None = None
        self._severity_filter: str = "all"
        self._search_active: bool = False
        self._search_text: str = ""

    def compose(self) -> ComposeResult:
        yield Grid(
            Static("", id="vuln_overlay_header"),
            Horizontal(
                VerticalScroll(Static("", id="vuln_overlay_list"), id="vuln_overlay_list_scroll"),
                VerticalScroll(Static("", id="vuln_overlay_detail"), id="vuln_overlay_detail_scroll"),
                id="vuln_overlay_main",
            ),
            Horizontal(
                Static("", id="vuln_overlay_keyhints"),
                Button("Copy Selected", variant="default", id="copy_overlay_selected"),
                Button("Copy All", variant="default", id="copy_overlay_all"),
                Button("Close", variant="default", id="close_vuln_overlay"),
                id="vuln_overlay_buttons",
            ),
            id="vuln_overlay_dialog",
        )

    _SEVERITY_COLORS: ClassVar[dict[str, str]] = {
        "critical": "#dc2626",
        "high": "#ea580c",
        "medium": "#d97706",
        "low": "#65a30d",
        "info": "#0284c7",
    }
    _SEVERITY_LABELS: ClassVar[dict[str, str]] = {
        "critical": "CRIT",
        "high": "HIGH",
        "medium": "MED",
        "low": "LOW",
        "info": "INFO",
    }
    _SEVERITY_ORDER: ClassVar[dict[str, int]] = {
        "critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4,
    }

    def on_mount(self) -> None:
        close_button = self.query_one("#close_vuln_overlay", Button)
        close_button.focus()
        self._refresh_view()
        self._refresh_timer = self.set_interval(0.5, self._refresh_view)

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def _get_app(self) -> "EspritTUIApp | None":
        app = self.app
        if isinstance(app, EspritTUIApp):
            return app
        return None

    def _get_vulnerabilities(self) -> list[dict[str, Any]]:
        app = self._get_app()
        if not app:
            return []
        return app._get_enriched_vulnerabilities()

    def _get_filtered_vulnerabilities(self) -> list[dict[str, Any]]:
        """Return vulnerabilities filtered by severity and search text."""
        vulns = self._get_vulnerabilities()
        if self._severity_filter != "all":
            vulns = [
                v for v in vulns
                if str(v.get("severity", "info")).lower() == self._severity_filter
            ]
        if self._search_text:
            query = self._search_text.lower()
            vulns = [
                v for v in vulns
                if query in str(v.get("title", "")).lower()
                or query in str(v.get("description", "")).lower()
            ]
        return vulns

    def _selected_vulnerability(self) -> dict[str, Any] | None:
        vulnerabilities = self._get_filtered_vulnerabilities()
        if not vulnerabilities:
            self._selected_index = 0
            return None
        if self._selected_index >= len(vulnerabilities):
            self._selected_index = len(vulnerabilities) - 1
        if self._selected_index < 0:
            self._selected_index = 0
        return vulnerabilities[self._selected_index]

    def _build_header(self, vulnerabilities: list[dict[str, Any]]) -> Text:
        """Build a summary header with severity breakdown."""
        text = Text()
        total = len(vulnerabilities)
        text.append(" VULNERABILITIES ", style="bold reverse #f97316")
        if total == 0:
            if self._severity_filter != "all" or self._search_text:
                text.append("  No matches", style="#d4d4d4")
            else:
                text.append("  None found yet", style="#d4d4d4")
            return text

        text.append(f"  {total} found", style="bold white")
        text.append("  ", style="")

        # Severity breakdown counts
        counts: dict[str, int] = {}
        for v in vulnerabilities:
            sev = str(v.get("severity", "info")).lower()
            counts[sev] = counts.get(sev, 0) + 1

        parts = []
        for sev_key in ("critical", "high", "medium", "low", "info"):
            c = counts.get(sev_key, 0)
            if c > 0:
                parts.append((c, sev_key))

        for i, (count, sev_key) in enumerate(parts):
            color = self._SEVERITY_COLORS.get(sev_key, "#6b7280")
            label = self._SEVERITY_LABELS.get(sev_key, sev_key.upper())
            if i > 0:
                text.append("  ", style="")
            text.append(f" {count} {label} ", style=f"bold {color}")

        return text

    def _build_keyhints(self) -> Text:
        """Build keyboard shortcut hints for the footer."""
        text = Text()
        if self._search_active:
            text.append(" / ", style="bold #22d3ee")
            text.append("search: ", style="#b8b8b8")
            text.append(self._search_text or "", style="bold white")
            text.append("▏", style="bold white")
            text.append("  ", style="")
            text.append(" esc ", style="bold #22d3ee")
            text.append("cancel", style="#b8b8b8")
            text.append("  ", style="")
            text.append(" enter ", style="bold #22d3ee")
            text.append("apply", style="#b8b8b8")
            return text
        hints = [
            ("\u2191\u2193/jk", "navigate"),
            ("c", "copy"),
            ("a", "copy all"),
            ("f", f"filter:{self._severity_filter}"),
            ("e", "export"),
            ("/", "search"),
            ("esc", "close"),
        ]
        for i, (key, desc) in enumerate(hints):
            if i > 0:
                text.append("  ", style="")
            text.append(f" {key} ", style="bold #22d3ee")
            text.append(desc, style="#b8b8b8")
        return text

    def _refresh_view(self) -> None:
        try:
            header = self.query_one("#vuln_overlay_header", Static)
            list_content = self.query_one("#vuln_overlay_list", Static)
            detail_content = self.query_one("#vuln_overlay_detail", Static)
            keyhints = self.query_one("#vuln_overlay_keyhints", Static)
        except (ValueError, Exception):
            return

        vulnerabilities = self._get_filtered_vulnerabilities()
        header.update(self._build_header(vulnerabilities))
        keyhints.update(self._build_keyhints())
        list_content.update(self._render_list(vulnerabilities))
        detail_content.update(self._render_detail())

    def _render_list(self, vulnerabilities: list[dict[str, Any]]) -> Text:
        text = Text()
        if not vulnerabilities:
            text.append("\n")
            text.append("  Waiting for findings...\n", style="dim italic")
            text.append("\n")
            text.append("  Vulnerabilities will appear here\n", style="dim")
            text.append("  in realtime as agents discover them.", style="dim")
            return text

        vulnerabilities.sort(
            key=lambda v: (
                self._SEVERITY_ORDER.get(str(v.get("severity", "")).lower(), 5),
                str(v.get("timestamp", "")),
            )
        )
        if self._selected_index >= len(vulnerabilities):
            self._selected_index = max(0, len(vulnerabilities) - 1)

        for idx, vuln in enumerate(vulnerabilities):
            severity = str(vuln.get("severity", "info")).lower()
            title = str(vuln.get("title", "Untitled Vulnerability"))
            cvss = vuln.get("cvss")
            target = vuln.get("target", "")
            endpoint = vuln.get("endpoint", "")

            severity_color = self._SEVERITY_COLORS.get(severity, "#6b7280")
            is_selected = idx == self._selected_index
            sev_label = self._SEVERITY_LABELS.get(severity, severity.upper())

            # Selection indicator
            if is_selected:
                text.append(" \u25b6 ", style=f"bold {severity_color}")
            else:
                text.append("   ", style="")

            # Severity badge
            text.append(f" {sev_label:4s} ", style=f"bold reverse {severity_color}")
            text.append(" ", style="")

            # CVSS score
            if cvss is not None:
                cvss_val = float(cvss)
                cvss_color = severity_color
                if cvss_val >= 9.0:
                    cvss_color = "#dc2626"
                elif cvss_val >= 7.0:
                    cvss_color = "#ea580c"
                text.append(f"{cvss_val:.1f}", style=f"bold {cvss_color}")
                text.append(" ", style="")
            else:
                text.append("     ", style="")

            # Title
            title_style = f"bold {severity_color}" if is_selected else "white"
            text.append(title, style=title_style)

            # Target/endpoint on next line
            if target or endpoint:
                text.append("\n")
                text.append("         ", style="")  # indent to align with title
                location = endpoint or target
                if len(location) > 50:
                    location = location[:47] + "..."
                text.append(location, style="#c2b0b0")

            # Separator between items
            if idx < len(vulnerabilities) - 1:
                text.append("\n")
                text.append("   \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n", style="#5b2222")
                text.append("\n")

        return text

    def _render_detail(self) -> Text:
        vulnerability = self._selected_vulnerability()
        if not vulnerability:
            text = Text()
            text.append("\n")
            text.append("  Select a vulnerability from\n", style="dim")
            text.append("  the list to inspect details.", style="dim")
            return text
        return self._render_detail_panel(vulnerability)

    def _render_detail_panel(self, vuln: dict[str, Any]) -> Text:
        """Render a rich detail panel for a vulnerability."""
        text = Text()
        severity = str(vuln.get("severity", "info")).lower()
        severity_color = self._SEVERITY_COLORS.get(severity, "#6b7280")
        sev_label = self._SEVERITY_LABELS.get(severity, severity.upper())

        # Title bar
        title = vuln.get("title", "Untitled Vulnerability")
        text.append(f" {sev_label} ", style=f"bold reverse {severity_color}")
        text.append(f" {title}", style="bold white")
        text.append("\n")

        # Metadata row
        cvss = vuln.get("cvss")
        if cvss is not None:
            cvss_val = float(cvss)
            cvss_color = severity_color
            if cvss_val >= 9.0:
                cvss_color = "#dc2626"
            elif cvss_val >= 7.0:
                cvss_color = "#ea580c"
            text.append(" CVSS ", style="dim")
            text.append(f"{cvss_val:.1f}", style=f"bold {cvss_color}")

        cve = vuln.get("cve", "")
        if cve:
            text.append("  ", style="")
            text.append(cve, style="bold #60a5fa")

        agent_name = vuln.get("agent_name", "")
        if agent_name:
            text.append("  ", style="")
            text.append(f"[{agent_name}]", style="dim #8a8a8a")

        text.append("\n")

        # Target info
        target = vuln.get("target", "")
        endpoint = vuln.get("endpoint", "")
        method = vuln.get("method", "")
        if target or endpoint or method:
            text.append(" \u2500\u2500 Target ", style="#9b6b6b")
            text.append("\u2500" * 30, style="#5b2222")
            text.append("\n")
            if target:
                text.append("  Target   ", style="bold #4ade80")
                text.append(f"{target}\n", style="white")
            if endpoint:
                text.append("  Endpoint ", style="bold #4ade80")
                text.append(f"{endpoint}\n", style="white")
            if method:
                text.append("  Method   ", style="bold #4ade80")
                text.append(f"{method}\n", style="white")

        # CVSS breakdown
        cvss_breakdown = vuln.get("cvss_breakdown", {})
        if cvss_breakdown:
            parts = []
            abbrevs = {
                "attack_vector": "AV", "attack_complexity": "AC",
                "privileges_required": "PR", "user_interaction": "UI",
                "scope": "S", "confidentiality": "C",
                "integrity": "I", "availability": "A",
            }
            for field, abbr in abbrevs.items():
                val = cvss_breakdown.get(field)
                if val:
                    parts.append(f"{abbr}:{val}")
            if parts:
                text.append("\n")
                text.append("  Vector ", style="#9b6b6b")
                text.append("/".join(parts), style="#d4d4d4")
                text.append("\n")

        # Description
        description = vuln.get("description", "")
        if description:
            text.append("\n")
            text.append(" \u2500\u2500 Description ", style="#9b6b6b")
            text.append("\u2500" * 26, style="#5b2222")
            text.append("\n")
            text.append(f"  {description}\n", style="white")

        # Impact
        impact = vuln.get("impact", "")
        if impact:
            text.append("\n")
            text.append(" \u2500\u2500 Impact ", style="#9b6b6b")
            text.append("\u2500" * 30, style="#5b2222")
            text.append("\n")
            text.append(f"  {impact}\n", style="white")

        # Technical Analysis
        technical_analysis = vuln.get("technical_analysis", "")
        if technical_analysis:
            text.append("\n")
            text.append(" \u2500\u2500 Technical Analysis ", style="#9b6b6b")
            text.append("\u2500" * 19, style="#5b2222")
            text.append("\n")
            text.append(f"  {technical_analysis}\n", style="white")

        # PoC
        poc_description = vuln.get("poc_description", "")
        poc_script_code = vuln.get("poc_script_code", "")
        if poc_description or poc_script_code:
            text.append("\n")
            text.append(" \u2500\u2500 Proof of Concept ", style="#9b6b6b")
            text.append("\u2500" * 20, style="#5b2222")
            text.append("\n")
            if poc_description:
                text.append(f"  {poc_description}\n", style="white")
            if poc_script_code:
                text.append("\n")
                text.append("  \u250c\u2500 code \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n", style="#7a4d4d")
                for line in poc_script_code.splitlines():
                    text.append(f"  \u2502 {line}\n", style="#a5d6a7")
                text.append("  \u2514\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n", style="#7a4d4d")

        # Code file / diff
        code_file = vuln.get("code_file", "")
        code_diff = vuln.get("code_diff", "")
        if code_file or code_diff:
            text.append("\n")
            text.append(" \u2500\u2500 Code Analysis ", style="#9b6b6b")
            text.append("\u2500" * 24, style="#5b2222")
            text.append("\n")
            if code_file:
                text.append(f"  File: {code_file}\n", style="#60a5fa")
            if code_diff:
                text.append("\n")
                for line in code_diff.splitlines():
                    if line.startswith("+"):
                        text.append(f"  {line}\n", style="#4ade80")
                    elif line.startswith("-"):
                        text.append(f"  {line}\n", style="#f87171")
                    else:
                        text.append(f"  {line}\n", style="dim")

        # Remediation
        remediation_steps = vuln.get("remediation_steps", "")
        if remediation_steps:
            text.append("\n")
            text.append(" \u2500\u2500 Remediation ", style="#9b6b6b")
            text.append("\u2500" * 25, style="#5b2222")
            text.append("\n")
            text.append(f"  {remediation_steps}\n", style="#fbbf24")

        return text

    def _move_selection(self, step: int) -> None:
        vulnerabilities = self._get_filtered_vulnerabilities()
        if not vulnerabilities:
            return
        self._selected_index = max(0, min(len(vulnerabilities) - 1, self._selected_index + step))
        self._refresh_view()

    def _copy_selected(self) -> None:
        vuln = self._selected_vulnerability()
        if not vuln:
            return
        markdown = VulnerabilityDetailScreen(vuln)._get_markdown_report()
        self.app.copy_to_clipboard(markdown)
        self._show_button_feedback("copy_overlay_selected", "Copied!")

    def _copy_all(self) -> None:
        vulnerabilities = self._get_filtered_vulnerabilities()
        if not vulnerabilities:
            return
        reports = [VulnerabilityDetailScreen(vuln)._get_markdown_report() for vuln in vulnerabilities]
        self.app.copy_to_clipboard("\n\n---\n\n".join(reports))
        self._show_button_feedback("copy_overlay_all", "Copied!")

    def _show_button_feedback(self, button_id: str, label: str) -> None:
        try:
            button = self.query_one(f"#{button_id}", Button)
        except (ValueError, Exception):
            return
        original_label = str(button.label)
        button.label = label
        self.set_timer(1.5, lambda: setattr(button, "label", original_label))

    def _cycle_severity_filter(self) -> None:
        """Cycle severity filter: all → critical → high → medium → low → info → all."""
        idx = self._SEVERITY_CYCLE.index(self._severity_filter)
        self._severity_filter = self._SEVERITY_CYCLE[(idx + 1) % len(self._SEVERITY_CYCLE)]
        self._selected_index = 0
        self._refresh_view()

    def _export_visible(self) -> None:
        """Export all visible (filtered) vulnerabilities as formatted text."""
        vulnerabilities = self._get_filtered_vulnerabilities()
        if not vulnerabilities:
            self.notify("No vulnerabilities to export.", severity="warning")
            return
        reports = [VulnerabilityDetailScreen(vuln)._get_markdown_report() for vuln in vulnerabilities]
        combined = "\n\n---\n\n".join(reports)
        try:
            self.app.copy_to_clipboard(combined)
            self.notify(f"Exported {len(vulnerabilities)} vulnerabilities to clipboard.")
        except Exception:
            self.notify(combined[:500] + "\n..." if len(combined) > 500 else combined)

    def on_key(self, event: events.Key) -> None:
        # Search mode: capture typed characters
        if self._search_active:
            if event.key == "escape":
                self._search_active = False
                self._search_text = ""
                self._selected_index = 0
                self._refresh_view()
            elif event.key == "enter":
                self._search_active = False
                self._refresh_view()
            elif event.key == "backspace":
                self._search_text = self._search_text[:-1]
                self._selected_index = 0
                self._refresh_view()
            elif event.character and event.character.isprintable():
                self._search_text += event.character
                self._selected_index = 0
                self._refresh_view()
            event.prevent_default()
            return
        if event.key in ("escape", "v"):
            self.app.pop_screen()
            event.prevent_default()
            return
        if event.key in ("up", "k"):
            self._move_selection(-1)
            event.prevent_default()
            return
        if event.key in ("down", "j"):
            self._move_selection(1)
            event.prevent_default()
            return
        if event.key == "c":
            self._copy_selected()
            event.prevent_default()
            return
        if event.key == "a":
            self._copy_all()
            event.prevent_default()
            return
        if event.key == "f":
            self._cycle_severity_filter()
            event.prevent_default()
            return
        if event.key == "e":
            self._export_visible()
            event.prevent_default()
            return
        if event.key == "slash":
            self._search_active = True
            self._search_text = ""
            self._refresh_view()
            event.prevent_default()
            return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "copy_overlay_selected":
            self._copy_selected()
        elif event.button.id == "copy_overlay_all":
            self._copy_all()
        elif event.button.id == "close_vuln_overlay":
            self.app.pop_screen()


class AgentHealthPopupScreen(ModalScreen):  # type: ignore[misc]
    """Live health diagnostics for all agents with intervention actions."""

    def __init__(self) -> None:
        super().__init__()
        self._selected_index = 0
        self._refresh_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        yield Grid(
            Static("", id="health_popup_status"),
            Horizontal(
                VerticalScroll(Static("", id="health_popup_list"), id="health_popup_list_scroll"),
                VerticalScroll(Static("", id="health_popup_detail"), id="health_popup_detail_scroll"),
                id="health_popup_main",
            ),
            Horizontal(
                Button("Stop Selected", variant="default", id="health_stop_selected"),
                Button("Retry Selected", variant="default", id="health_retry_selected"),
                Button("Close", variant="default", id="close_health_popup"),
                id="health_popup_buttons",
            ),
            id="health_popup_dialog",
        )

    def on_mount(self) -> None:
        close_button = self.query_one("#close_health_popup", Button)
        close_button.focus()
        self._refresh_view()
        self._refresh_timer = self.set_interval(0.5, self._refresh_view)

    def on_unmount(self) -> None:
        if self._refresh_timer is not None:
            self._refresh_timer.stop()
            self._refresh_timer = None

    def _get_app(self) -> "EspritTUIApp | None":
        app = self.app
        if isinstance(app, EspritTUIApp):
            return app
        return None

    def _get_health_rows(self) -> list[dict[str, Any]]:
        app = self._get_app()
        if not app:
            return []
        return app._get_agent_health_rows()

    def _selected_row(self) -> dict[str, Any] | None:
        rows = self._get_health_rows()
        if not rows:
            self._selected_index = 0
            return None
        if self._selected_index >= len(rows):
            self._selected_index = len(rows) - 1
        if self._selected_index < 0:
            self._selected_index = 0
        return rows[self._selected_index]

    def _refresh_view(self) -> None:
        try:
            status = self.query_one("#health_popup_status", Static)
            list_content = self.query_one("#health_popup_list", Static)
            detail_content = self.query_one("#health_popup_detail", Static)
        except (ValueError, Exception):
            return

        app = self._get_app()
        if app:
            status.update(app._build_global_status_snapshot_text())
        else:
            status.update(Text("Status unavailable", style="dim"))

        rows = self._get_health_rows()
        list_content.update(self._render_list(rows))
        detail_content.update(self._render_detail())

    def _render_list(self, rows: list[dict[str, Any]]) -> Text:
        text = Text()
        if not rows:
            text.append("No agents yet.\n", style="dim")
            text.append("Agent health will appear once scan starts.", style="dim italic")
            return text

        for idx, row in enumerate(rows):
            risk = row.get("risk", "low")
            color = {"high": "#dc2626", "medium": "#d97706", "low": "#22c55e"}.get(risk, "#22c55e")
            marker = "▶ " if idx == self._selected_index else "  "
            text.append(marker, style=f"bold {color}")
            text.append(f"[{risk.upper():6s}] ", style=color)
            text.append(str(row.get("name", row.get("agent_id", "Agent"))), style="bold white")
            text.append(f"  {row.get('status', 'unknown')}", style="dim")
            text.append("\n  ", style="dim")
            text.append(
                f"age {row.get('last_output_age', '--')} · errors {row.get('error_streak', 0)} · retries {row.get('retry_count', 0)}",
                style="dim",
            )
            if idx < len(rows) - 1:
                text.append("\n")

        return text

    def _render_detail(self) -> Text:
        row = self._selected_row()
        text = Text()
        if not row:
            text.append("Select an agent to inspect diagnostics.", style="dim")
            return text

        text.append("Agent Health\n", style="bold #22d3ee")
        text.append("Agent: ", style="bold #4ade80")
        text.append(f"{row.get('name', 'Unknown')}\n")
        text.append("Status: ", style="bold #4ade80")
        text.append(f"{row.get('status', 'unknown')}\n")
        text.append("Risk: ", style="bold #4ade80")
        text.append(f"{row.get('risk', 'low').upper()}\n")
        text.append("Last Output Age: ", style="bold #4ade80")
        text.append(f"{row.get('last_output_age', '--')}\n")
        text.append("Error Streak: ", style="bold #4ade80")
        text.append(f"{row.get('error_streak', 0)}\n")
        text.append("Retry Count: ", style="bold #4ade80")
        text.append(f"{row.get('retry_count', 0)}\n\n")

        snippet = row.get("snippet")
        if snippet:
            text.append("Latest Activity\n", style="bold #4ade80")
            text.append(str(snippet), style="white")
        else:
            text.append("No activity snippet available yet.", style="dim")

        return text

    def _move_selection(self, step: int) -> None:
        rows = self._get_health_rows()
        if not rows:
            return
        self._selected_index = max(0, min(len(rows) - 1, self._selected_index + step))
        self._refresh_view()

    def _stop_selected(self) -> None:
        row = self._selected_row()
        app = self._get_app()
        if not row or not app:
            return
        agent_id = str(row.get("agent_id", ""))
        if not agent_id:
            return
        success = app._request_stop_agent(agent_id)
        self._show_button_feedback("health_stop_selected", "Stopped" if success else "Failed")

    def _retry_selected(self) -> None:
        row = self._selected_row()
        app = self._get_app()
        if not row or not app:
            return
        agent_id = str(row.get("agent_id", ""))
        if not agent_id:
            return
        success = app.retry_agent(agent_id)
        self._show_button_feedback("health_retry_selected", "Retried" if success else "Failed")

    def _show_button_feedback(self, button_id: str, label: str) -> None:
        try:
            button = self.query_one(f"#{button_id}", Button)
        except (ValueError, Exception):
            return
        original_label = str(button.label)
        original_variant = button.variant
        button.label = label
        button.variant = "success"
        self.set_timer(2.5, lambda: (setattr(button, "label", original_label), setattr(button, "variant", original_variant)))

    def on_key(self, event: events.Key) -> None:
        if event.key in ("escape", "h"):
            self.app.pop_screen()
            event.prevent_default()
            return
        if event.key in ("up", "k"):
            self._move_selection(-1)
            event.prevent_default()
            return
        if event.key in ("down", "j"):
            self._move_selection(1)
            event.prevent_default()
            return
        if event.key == "s":
            self._stop_selected()
            event.prevent_default()
            return
        if event.key == "r":
            self._retry_selected()
            event.prevent_default()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "health_stop_selected":
            self._stop_selected()
        elif event.button.id == "health_retry_selected":
            self._retry_selected()
        elif event.button.id == "close_health_popup":
            self.app.pop_screen()


class QuitScreen(ModalScreen):  # type: ignore[misc]
    def compose(self) -> ComposeResult:
        yield Grid(
            Label("Quit Esprit?", id="quit_title"),
            Grid(
                Button("Yes", variant="error", id="quit"),
                Button("No", variant="default", id="cancel"),
                id="quit_buttons",
            ),
            id="quit_dialog",
        )

    def on_mount(self) -> None:
        quit_button = self.query_one("#quit", Button)
        quit_button.focus()

    def on_key(self, event: events.Key) -> None:
        if event.key in ("left", "right", "up", "down"):
            focused = self.focused

            if focused and focused.id == "quit":
                cancel_button = self.query_one("#cancel", Button)
                cancel_button.focus()
            else:
                quit_button = self.query_one("#quit", Button)
                quit_button.focus()

            event.prevent_default()
        elif event.key == "enter":
            focused = self.focused
            if focused and isinstance(focused, Button):
                focused.press()
            event.prevent_default()
        elif event.key == "escape":
            self.app.pop_screen()
            event.prevent_default()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "quit":
            self.app.action_custom_quit()
        else:
            self.app.pop_screen()


class UpdateScreen(ModalScreen):  # type: ignore[misc]
    """Update notification modal — shown automatically at startup or via Ctrl+U."""

    def __init__(
        self,
        update_info: "UpdateInfo | None" = None,
        checking: bool = False,
    ) -> None:
        super().__init__()
        self._update_info = update_info
        self._checking = checking

    def compose(self) -> ComposeResult:
        yield Grid(
            Label("", id="update_title"),
            Label("", id="update_body"),
            Grid(
                Button("Update Now", variant="success", id="update_now_btn"),
                Button("Next Launch", variant="default", id="update_next_btn"),
                Button("Skip", variant="default", id="update_skip_btn"),
                id="update_action_buttons",
            ),
            Button("OK", variant="default", id="update_ok_btn"),
            id="update_dialog",
        )

    def on_mount(self) -> None:
        self._render_state()
        if self._checking:
            threading.Thread(target=self._run_check, daemon=True).start()

    def _render_state(self) -> None:
        from esprit.interface.updater import _current_version

        title = self.query_one("#update_title", Label)
        body = self.query_one("#update_body", Label)
        action_btns = self.query_one("#update_action_buttons")
        ok_btn = self.query_one("#update_ok_btn", Button)

        if self._checking:
            title.update("Checking for Updates")
            body.update("Connecting to GitHub…")
            action_btns.display = False
            ok_btn.display = False
        elif self._update_info is not None:
            title.update("Update Available")
            body.update(
                f"Esprit v{self._update_info.latest} is available"
                f"  ·  you have v{self._update_info.current}"
            )
            action_btns.display = True
            ok_btn.display = False
            self.query_one("#update_now_btn", Button).focus()
        else:
            current = _current_version()
            title.update("Esprit is up to date")
            body.update(f"You're running v{current}, the latest version.")
            action_btns.display = False
            ok_btn.display = True
            ok_btn.focus()

    def _run_check(self) -> None:
        from esprit.interface.updater import check_for_update

        result = check_for_update(force=True)
        self.app.call_from_thread(self._on_check_done, result)

    def _on_check_done(self, update_info: "UpdateInfo | None") -> None:
        self._checking = False
        self._update_info = update_info
        self._render_state()

    def on_key(self, event: events.Key) -> None:
        if event.key in ("left", "right", "up", "down", "tab"):
            buttons = [b for b in self.query(Button) if b.display]
            if not buttons:
                return
            try:
                focused = self.focused
                idx = buttons.index(focused) if focused in buttons else -1  # type: ignore[arg-type]
                if event.key in ("right", "down", "tab"):
                    buttons[(idx + 1) % len(buttons)].focus()
                else:
                    buttons[(idx - 1) % len(buttons)].focus()
            except (ValueError, Exception):
                buttons[0].focus()
            event.prevent_default()
        elif event.key == "escape":
            self.app.pop_screen()
            event.prevent_default()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "update_now_btn":
            self.app.exit("update_now")
        elif btn_id == "update_next_btn":
            from esprit.interface.updater import schedule_update

            schedule_update()
            self.app.pop_screen()
            try:
                tokens = get_theme_tokens(Config.get_launchpad_theme())
                success_color = str(tokens.get("success", "#22c55e"))
                keymap = self.app.query_one("#keymap_indicator", Static)
                msg = Text()
                msg.append("✓ ", style=f"bold {success_color}")
                msg.append("Update scheduled — will apply on next launch", style="dim")
                keymap.update(msg)
            except Exception:
                logging.debug("Update application failed", exc_info=True)
        else:  # skip or ok
            self.app.pop_screen()


class EspritTUIApp(App):  # type: ignore[misc]
    CSS_PATH = "assets/tui_styles.tcss"
    DEFAULT_THEME = DEFAULT_THEME_ID
    SUPPORTED_THEMES: ClassVar[tuple[str, ...]] = SUPPORTED_THEME_IDS

    LEFT_ONLY_LAYOUT_MIN_WIDTH = 120
    THREE_PANE_LAYOUT_MIN_WIDTH = 170
    RUN_STATUS_FRAMES: ClassVar[tuple[str, ...]] = ("|", "/", "-", "\\")

    selected_agent_id: reactive[str | None] = reactive(default=None)
    show_splash: reactive[bool] = reactive(default=True)

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f1", "toggle_help", "Help", priority=True),
        Binding("ctrl+q", "request_quit", "Quit", priority=True),
        Binding("ctrl+c", "request_quit", "Quit", priority=True),
        Binding("escape", "stop_selected_agent", "Stop Agent", priority=True),
        Binding("b", "show_browser_preview", "Browser Preview", priority=False),
        Binding("ctrl+v", "toggle_vulnerability_overlay", "Vulnerabilities", priority=False),
        Binding("ctrl+h", "toggle_agent_health_popup", "Agent Health", priority=False),
        Binding("ctrl+u", "check_for_updates", "Updates", priority=True),
        Binding("question_mark", "show_help_overlay", "Help Shortcuts", priority=True),
        Binding("ctrl+l", "clear_chat", "Clear Chat", priority=True),
    ]

    def __init__(self, args: argparse.Namespace, gui_server: _GUIServerType = None):
        super().__init__()
        self.args = args
        self._gui_server = gui_server
        self._theme_id = self._normalize_theme_id(Config.get_launchpad_theme())
        self.scan_config = self._build_scan_config(args)
        self.agent_config = self._build_agent_config(args)

        self.tracer = Tracer(self.scan_config["run_name"])
        self.tracer.set_scan_config(self.scan_config)
        set_global_tracer(self.tracer)

        self.agent_nodes: dict[str, TreeNode] = {}

        self._displayed_agents: set[str] = set()
        self._displayed_events: list[str] = []

        self._streaming_render_cache: dict[str, tuple[int, Any]] = {}
        self._last_streaming_len: dict[str, int] = {}
        self._streaming_start_time: dict[str, float] = {}
        self._streaming_start_output_tokens: dict[str, int] = {}

        self._scan_thread: threading.Thread | None = None
        self._scan_stop_event = threading.Event()
        self._scan_completed = threading.Event()
        self._scan_failed = threading.Event()
        self._stats_spinner_frame: int = 0

        self._spinner_frame_index: int = 0  # Current animation frame index
        self._sweep_num_squares: int = 6  # Number of squares in sweep animation
        palette = self._theme_palette()
        self._sweep_colors: list[str] = list(palette.get("sweep_colors", []))
        self._compact_sweep_colors: list[str] = list(palette.get("compact_sweep_colors", []))
        self._shimmer_colors: list[str] = list(palette.get("shimmer_colors", []))
        self._sweep_dot_color = str(palette.get("sweep_dot_color", "#0a3d1f"))
        self._dot_animation_timer: Any | None = None

        self._previously_compacting: set[str] = set()
        self._compaction_done_until: dict[str, float] = {}

        self._setup_cleanup_handlers()

    @classmethod
    def _normalize_theme_id(cls, theme_id: str | None) -> str:
        return normalize_theme_id(theme_id)

    def _theme_palette(self) -> dict[str, Any]:
        theme_id = self._normalize_theme_id(getattr(self, "_theme_id", self.DEFAULT_THEME))
        return get_theme_tokens(theme_id)

    def _marker_color(self, marker: str) -> str:
        return get_marker_color(self._theme_palette(), marker)

    def _marker_style(self, marker: str, *, bold: bool = True) -> str:
        color = self._marker_color(marker)
        return f"bold {color}" if bold else color

    def _apply_theme_class(self) -> None:
        try:
            screen = self.screen
        except Exception:
            return

        for theme_id in self.SUPPORTED_THEMES:
            screen.remove_class(f"theme-{theme_id}")
        screen.add_class(f"theme-{self._theme_id}")

    def _build_scan_config(self, args: argparse.Namespace) -> dict[str, Any]:
        return {
            "scan_id": args.run_name,
            "targets": args.targets_info,
            "user_instructions": args.instruction or "",
            "run_name": args.run_name,
        }

    def _build_agent_config(self, args: argparse.Namespace) -> dict[str, Any]:
        scan_mode = getattr(args, "scan_mode", "deep")
        llm_config = LLMConfig(scan_mode=scan_mode)

        config = {
            "llm_config": llm_config,
            "max_iterations": 300,
            "targets": args.targets_info,
        }

        if getattr(args, "local_sources", None):
            config["local_sources"] = args.local_sources
        if getattr(args, "local_artifacts", None):
            config["local_artifacts"] = args.local_artifacts

        return config

    def _setup_cleanup_handlers(self) -> None:
        def cleanup_on_exit() -> None:
            from esprit.runtime import cleanup_runtime, extract_and_save_diffs

            # Extract file edits from sandbox BEFORE destroying it
            if hasattr(self, "agent") and hasattr(self.agent, "state") and self.agent.state.sandbox_id:
                extract_and_save_diffs(self.agent.state.sandbox_id)

            self.tracer.cleanup()
            cleanup_runtime()

        def signal_handler(_signum: int, _frame: Any) -> None:
            self.tracer.cleanup()
            sys.exit(0)

        atexit.register(cleanup_on_exit)
        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
        if hasattr(signal, "SIGHUP"):
            signal.signal(signal.SIGHUP, signal_handler)

    def compose(self) -> ComposeResult:
        if self.show_splash:
            yield SplashScreen(id="splash_screen")

    def watch_show_splash(self, show_splash: bool) -> None:
        if not show_splash and self.is_mounted:
            try:
                splash = self.query_one("#splash_screen")
                splash.remove()
            except ValueError:
                pass

            main_container = Vertical(id="main_container")

            self.mount(main_container)

            content_container = Horizontal(id="content_container")
            main_container.mount(content_container)

            left_panel_header = Static("Subagents  [Ctrl+H: Health]", id="left_panel_header")

            agents_tree = Tree("Active Agents", id="agents_tree")
            agents_tree.root.expand()
            agents_tree.show_root = False

            agents_tree.show_guide = True
            agents_tree.guide_depth = 3
            agents_tree.guide_style = "dashed"

            stats_display = Static("", id="stats_display")

            left_panel = Vertical(left_panel_header, agents_tree, stats_display, id="left_panel")

            center_panel_header = Static("Live Stream", id="center_panel_header")

            chat_area_container = Vertical(center_panel_header, id="chat_area_container")

            chat_display = Static("", id="chat_display")
            chat_history = VerticalScroll(chat_display, id="chat_history")
            chat_history.can_focus = True

            status_text = Static("", id="status_text")
            keymap_indicator = Static("", id="keymap_indicator")

            agent_status_display = Horizontal(
                status_text, keymap_indicator, id="agent_status_display", classes="hidden"
            )

            chat_prompt = Static("> ", id="chat_prompt")
            chat_input = ChatTextArea(
                "",
                id="chat_input",
                show_line_numbers=False,
            )
            chat_input.set_app_reference(self)
            chat_input_container = Horizontal(chat_prompt, chat_input, id="chat_input_container")

            right_panel_header = Static("Vulnerabilities  [Ctrl+V]", id="right_panel_header")

            vulnerabilities_panel = VulnerabilitiesPanel(id="vulnerabilities_panel")

            right_panel = Vertical(right_panel_header, vulnerabilities_panel, id="right_panel")

            content_container.mount(left_panel)
            content_container.mount(chat_area_container)
            content_container.mount(right_panel)

            chat_area_container.mount(chat_history)
            chat_area_container.mount(agent_status_display)
            chat_area_container.mount(chat_input_container)

            self._apply_responsive_layout(self.size.width)

            self.call_after_refresh(self._focus_chat_input)

    def _focus_chat_input(self) -> None:
        if len(self.screen_stack) > 1 or self.show_splash:
            return

        if not self.is_mounted:
            return

        try:
            chat_input = self.query_one("#chat_input", ChatTextArea)
            chat_input.show_vertical_scrollbar = False
            chat_input.show_horizontal_scrollbar = False
            chat_input.focus()
        except (ValueError, Exception):
            self.call_after_refresh(self._focus_chat_input)

    def _focus_agents_tree(self) -> None:
        if len(self.screen_stack) > 1 or self.show_splash:
            return

        if not self.is_mounted:
            return

        try:
            agents_tree = self.query_one("#agents_tree", Tree)
            agents_tree.focus()

            if agents_tree.root.children:
                first_node = agents_tree.root.children[0]
                agents_tree.select_node(first_node)
        except (ValueError, Exception):
            self.call_after_refresh(self._focus_agents_tree)

    def on_mount(self) -> None:
        self.title = "esprit"
        self._apply_theme_class()

        self.set_timer(4.5, self._hide_splash_screen)

    def _hide_splash_screen(self) -> None:
        self.show_splash = False

        self._start_scan_thread()

        # Start GUI server (always available for live dashboard)
        if self._gui_server is not None:
            try:
                self._gui_server.start(self.tracer)
            except Exception:  # noqa: BLE001
                logging.debug("Failed to start GUI server", exc_info=True)

        self.set_interval(0.35, self._update_ui_from_tracer)

        # Background update check — uses a 24 h cache so it's essentially instant
        # on repeat launches.  Notification is shown 3 s after the TUI starts.
        threading.Thread(target=self._background_update_check, daemon=True).start()

    def _background_update_check(self) -> None:
        """Run in background thread; push UpdateScreen if a newer version exists."""
        try:
            from esprit.interface.updater import check_for_update

            info = check_for_update()
            if info is not None:
                time.sleep(3.0)  # Let the TUI fully render before interrupting
                self.call_from_thread(self._show_update_notification, info)
        except Exception:
            logging.debug("Update check failed", exc_info=True)

    def _show_error_notification(self, error_message: str) -> None:
        """Display an error notification as a red-bordered panel in the chat area."""
        logging.error("UI error notification: %s", error_message)
        try:
            chat_history = self.query_one("#chat_history", VerticalScroll)
            panel = Panel(
                Text(error_message, style="bold red"),
                border_style="red",
                title="Error",
                expand=True,
            )
            error_widget = Static(panel)
            try:
                is_at_bottom = chat_history.scroll_y >= (chat_history.max_scroll_y - 50)
            except (AttributeError, ValueError):
                is_at_bottom = True
            chat_history.mount(error_widget)
            if is_at_bottom:
                self.call_later(chat_history.scroll_end, animate=False)
        except Exception:
            logging.debug("Failed to display error notification in UI", exc_info=True)

    def _show_update_notification(self, info: "UpdateInfo") -> None:
        """Called on the main thread after a background update check finds a new version."""
        if not self.is_mounted or self.show_splash:
            return
        if len(self.screen_stack) > 1:
            return  # Another modal is already open; skip silently
        self.push_screen(UpdateScreen(update_info=info))

    def _update_ui_from_tracer(self) -> None:
        if self.show_splash:
            return

        if len(self.screen_stack) > 1:
            return

        if not self.is_mounted:
            return

        try:
            chat_history = self.query_one("#chat_history", VerticalScroll)
            agents_tree = self.query_one("#agents_tree", Tree)

            if not self._is_widget_safe(chat_history) or not self._is_widget_safe(agents_tree):
                return
        except (ValueError, Exception):
            return

        agent_updates = False
        for agent_id, agent_data in list(self.tracer.agents.items()):
            if agent_id not in self._displayed_agents:
                self._add_agent_node(agent_data)
                self._displayed_agents.add(agent_id)
                agent_updates = True
            elif self._update_agent_node(agent_id, agent_data):
                agent_updates = True

        if agent_updates:
            self._expand_new_agent_nodes()

        self._update_chat_view()

        self._update_streaming_timing()

        self._track_compaction_transitions()

        self._update_agent_status_display()

        self._update_stats_display()

        self._update_vulnerabilities_panel()

        self._cleanup_browser_screenshots()

    def _cleanup_browser_screenshots(self) -> None:
        """Free memory by replacing older browser screenshots with a placeholder.

        Keeps only the latest screenshot per agent in the tracer's tool_executions.
        """
        # Group browser_action executions by agent_id
        agent_screenshot_ids: dict[str, list[int]] = {}
        for exec_id, tool_data in list(self.tracer.tool_executions.items()):
            if tool_data.get("tool_name") != "browser_action":
                continue
            result = tool_data.get("result")
            if not isinstance(result, dict):
                continue
            screenshot = result.get("screenshot")
            if not screenshot or not isinstance(screenshot, str) or screenshot == "[rendered]":
                continue
            agent_id = tool_data.get("agent_id", "")
            agent_screenshot_ids.setdefault(agent_id, []).append(exec_id)

        # For each agent, keep only the latest screenshot
        for agent_id, exec_ids in agent_screenshot_ids.items():
            if len(exec_ids) <= 1:
                # Track the latest one
                if exec_ids:
                    self.tracer.latest_browser_screenshots[agent_id] = exec_ids[0]
                continue

            # Sort by execution id (higher = newer)
            exec_ids.sort()
            latest_id = exec_ids[-1]
            self.tracer.latest_browser_screenshots[agent_id] = latest_id

            # Replace older screenshots with placeholder
            for eid in exec_ids[:-1]:
                result = self.tracer.tool_executions[eid].get("result")
                if isinstance(result, dict) and result.get("screenshot"):
                    result["screenshot"] = "[rendered]"

    def _running_status_frame(self) -> str:
        frame_index = self._stats_spinner_frame % len(self.RUN_STATUS_FRAMES)
        return self.RUN_STATUS_FRAMES[frame_index]

    def _agent_status_marker(self, status: str) -> str:
        status_markers = {
            "running": self._running_status_frame(),
            "waiting": "~",
            "completed": "●",
            "failed": "x",
            "stopped": "-",
            "stopping": ".",
            "llm_failed": "x",
        }
        return status_markers.get(status, ".")

    def _update_agent_node(self, agent_id: str, agent_data: dict[str, Any]) -> bool:
        if agent_id not in self.agent_nodes:
            return False

        try:
            agent_node = self.agent_nodes[agent_id]
            agent_name_raw = agent_data.get("name", "Agent")
            status = agent_data.get("status", "running")
            status_icon = self._agent_status_marker(status)
            vuln_count = self._agent_vulnerability_count(agent_id)
            vuln_indicator = f" ({vuln_count})" if vuln_count > 0 else ""
            agent_name = f"{status_icon} {agent_name_raw}{vuln_indicator}"

            if agent_node.label != agent_name:
                agent_node.set_label(agent_name)
                return True

        except (KeyError, AttributeError, ValueError) as e:
            logging.warning(f"Failed to update agent node label: {e}")

        return False

    def _get_chat_content(
        self,
    ) -> tuple[Any, str | None]:
        if not self.selected_agent_id:
            return self._get_chat_placeholder_content(
                "Select an agent from the tree to see its activity.", "placeholder-no-agent"
            )

        events = self._gather_agent_events(self.selected_agent_id)
        streaming = self.tracer.get_streaming_content(self.selected_agent_id)

        # Check if this is the root agent with children
        is_root = (
            self.selected_agent_id == self._get_root_agent_id()
            and len(self._get_child_agents(self.selected_agent_id)) > 0
        )

        if not events and not streaming and not is_root:
            return self._get_chat_placeholder_content(
                "Starting agent...", "placeholder-no-activity"
            )

        current_event_ids = [e["id"] for e in events]
        current_streaming_len = len(streaming) if streaming else 0
        last_streaming_len = self._last_streaming_len.get(self.selected_agent_id, 0)

        # Skip cache when root has running children (shimmer animation needs refresh)
        has_running = is_root and self._has_running_children(self.selected_agent_id)

        if (
            not has_running
            and current_event_ids == self._displayed_events
            and current_streaming_len == last_streaming_len
        ):
            return None, None

        self._displayed_events = current_event_ids
        self._last_streaming_len[self.selected_agent_id] = current_streaming_len

        rendered = self._get_rendered_events_content(events)

        # Append subagent dashboard for the root agent
        if is_root:
            dashboard = self._build_subagent_dashboard(self.selected_agent_id)
            if dashboard:
                parts: list[Any] = []
                if rendered and not isinstance(rendered, Text) or (isinstance(rendered, Text) and rendered.plain.strip()):
                    parts.append(rendered)
                    parts.append(Text(""))
                parts.append(dashboard)
                rendered = Group(*parts) if len(parts) > 1 else parts[0]

        return rendered, "chat-content"

    def _update_chat_view(self) -> None:
        if len(self.screen_stack) > 1 or self.show_splash or not self.is_mounted:
            return

        try:
            chat_history = self.query_one("#chat_history", VerticalScroll)
        except (ValueError, Exception):
            return

        if not self._is_widget_safe(chat_history):
            return

        try:
            is_at_bottom = chat_history.scroll_y >= (chat_history.max_scroll_y - 50)
        except (AttributeError, ValueError):
            is_at_bottom = True

        content, css_class = self._get_chat_content()
        if content is None:
            return

        chat_display = self.query_one("#chat_display", Static)
        self._safe_widget_operation(chat_display.update, content)
        chat_display.set_classes(css_class)

        if is_at_bottom:
            self.call_later(chat_history.scroll_end, animate=False)

    def _get_chat_placeholder_content(
        self, message: str, placeholder_class: str
    ) -> tuple[Text, str]:
        self._displayed_events = [placeholder_class]
        text = Text()
        text.append(message)
        return text, f"chat-placeholder {placeholder_class}"

    def _get_rendered_events_content(self, events: list[dict[str, Any]]) -> Any:
        renderables: list[Any] = []

        if not events:
            return Text()

        for event in events:
            content: Any = None

            if event["type"] == "chat":
                content = self._render_chat_content(event["data"])
            elif event["type"] == "tool":
                content = self._render_tool_content_simple(event["data"])

            if content:
                if renderables:
                    renderables.append(Text(""))
                renderables.append(content)

        if self.selected_agent_id:
            streaming = self.tracer.get_streaming_content(self.selected_agent_id)
            if streaming:
                streaming_text = self._render_streaming_content(streaming)
                if streaming_text:
                    if renderables:
                        renderables.append(Text(""))
                    renderables.append(streaming_text)

            # Show compacting memory indicator in the chat stream
            if self.selected_agent_id in self.tracer.compacting_agents:
                compact_indicator = self._render_compacting_indicator()
                if renderables:
                    renderables.append(Text(""))
                renderables.append(compact_indicator)

        if not renderables:
            return Text()

        if len(renderables) == 1:
            return renderables[0]

        return Group(*renderables)

    def _render_compacting_indicator(self) -> Text:
        """Render an inline compacting-memory indicator for the chat stream."""
        palette = self._theme_palette()
        compact_primary = str(palette.get("compacting_primary", "#fbbf24"))
        compact_secondary = str(palette.get("compacting_secondary", "#d97706"))
        text = Text()
        # Animated spinner using the stats spinner frame
        frames = ["◐", "◓", "◑", "◒"]
        frame = frames[self._stats_spinner_frame % len(frames)]
        text.append(f" {frame} ", style=compact_primary)
        text.append("Compacting memory", style=f"{compact_secondary} bold")
        text.append("  ·  ", style="dim")
        text.append("summarizing older messages to free context", style="dim")
        return text

    def _track_compaction_transitions(self) -> None:
        """Detect agents that just finished compaction and schedule a 2s done message."""
        import time as _time

        current = set(self.tracer.compacting_agents)
        just_finished = self._previously_compacting - current
        for agent_id in just_finished:
            self._compaction_done_until[agent_id] = _time.monotonic() + 2.0
        self._previously_compacting = current

    # ------------------------------------------------------------------
    # Shimmer text + subagent dashboard
    # ------------------------------------------------------------------

    def _shimmer_text(self, content: str, max_len: int = 120) -> Text:
        """Create text with a sweeping shimmer gradient animation."""
        if len(content) > max_len:
            content = content[: max_len - 1] + "…"
        text = Text()
        if not content:
            return text

        colors = self._shimmer_colors
        half_w = len(colors) // 2  # radius of the bright region
        text_len = len(content)

        # Sweep position advances each frame; cycle across the full text
        cycle = text_len + len(colors)
        sweep_center = (self._stats_spinner_frame * 3) % cycle - half_w

        for i, char in enumerate(content):
            dist = abs(i - sweep_center)
            if dist <= half_w:
                color = colors[half_w - dist]
            else:
                color = colors[0]
            text.append(char, style=Style(color=color))

        return text

    def _get_root_agent_id(self) -> str | None:
        """Return the root agent id (the agent with no parent)."""
        for agent_id, data in list(self.tracer.agents.items()):
            if data.get("parent_id") is None:
                return agent_id
        return None

    def _get_child_agents(self, parent_id: str) -> list[dict[str, Any]]:
        """Return child agent data dicts for the given parent."""
        return [
            data
            for data in list(self.tracer.agents.values())
            if data.get("parent_id") == parent_id
        ]

    def _get_agent_snippet(self, agent_id: str) -> str | None:
        """Get the latest activity snippet for an agent (streaming or last message)."""
        # Prefer current streaming content
        get_streaming = getattr(self.tracer, "get_streaming_content", None)
        streaming = get_streaming(agent_id) if callable(get_streaming) else None
        if streaming and streaming.strip():
            # Take the last non-empty line(s)
            lines = [ln for ln in streaming.strip().splitlines() if ln.strip()]
            if lines:
                return lines[-1].strip()

        # Fall back to the most recent chat message
        agent_msgs = [
            m for m in reversed(list(getattr(self.tracer, "chat_messages", [])))
            if m.get("agent_id") == agent_id and m.get("role") == "assistant"
        ]
        if agent_msgs:
            content = agent_msgs[0].get("content", "")
            lines = [ln for ln in content.strip().splitlines() if ln.strip()]
            if lines:
                return lines[-1].strip()

        # Fall back to last tool being used
        agent_tools = [
            t for t in list(getattr(self.tracer, "tool_executions", {}).values())
            if t.get("agent_id") == agent_id
        ]
        if agent_tools:
            last_tool = max(agent_tools, key=lambda t: t.get("timestamp", ""))
            tool_name = last_tool.get("tool_name", "")
            if tool_name and tool_name not in ("scan_start_info", "subagent_start_info"):
                status = last_tool.get("status", "running")
                prefix = "Using" if status == "running" else "Used"
                return f"{prefix} {tool_name}"

        return None

    @staticmethod
    def _parse_iso(ts: str | None) -> datetime | None:
        if not ts:
            return None
        normalized = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    @staticmethod
    def _format_short_duration(total_seconds: float) -> str:
        if total_seconds < 0:
            total_seconds = 0
        seconds = int(total_seconds)
        hours = seconds // 3600
        minutes = (seconds % 3600) // 60
        remaining = seconds % 60
        if hours > 0:
            return f"{hours}h {minutes:02d}m"
        if minutes > 0:
            return f"{minutes}m {remaining:02d}s"
        return f"{remaining}s"

    def _get_agent_elapsed_seconds(self, agent_data: dict[str, Any]) -> float:
        created = self._parse_iso(str(agent_data.get("created_at") or ""))
        if not created:
            created = self._parse_iso(str(getattr(self.tracer, "start_time", "") or ""))
        if not created:
            return 0.0

        status = str(agent_data.get("status", "running"))
        completed_states = {"completed", "failed", "stopped", "llm_failed", "error", "sandbox_failed"}
        end_ts = (
            str(getattr(self.tracer, "end_time", "") or "")
            if status in completed_states
            else datetime.now(UTC).isoformat()
        )
        end = self._parse_iso(end_ts)
        if not end:
            return 0.0
        return max(0.0, (end - created).total_seconds())

    def _get_agent_token_snapshot(self, agent_id: str) -> tuple[int, float]:
        try:
            llm_stats = self.tracer.get_total_llm_stats()
        except Exception:  # noqa: BLE001
            return (0, 0.0)

        by_agent = llm_stats.get("by_agent", {})
        agent_stats = by_agent.get(str(agent_id), {})
        input_tokens = int(agent_stats.get("input_tokens", 0))
        output_tokens = int(agent_stats.get("output_tokens", 0))
        total_tokens = max(0, input_tokens + output_tokens)
        cost = float(agent_stats.get("cost", 0.0))
        return (total_tokens, cost)

    def _get_tool_activity_snippet(self, agent_id: str) -> str | None:
        tools = [
            tool
            for tool in list(getattr(self.tracer, "tool_executions", {}).values())
            if tool.get("agent_id") == agent_id
        ]
        if not tools:
            return None

        latest = max(
            tools,
            key=lambda t: (
                str(t.get("completed_at") or ""),
                str(t.get("timestamp") or ""),
                int(t.get("execution_id") or 0),
            ),
        )
        tool_name = str(latest.get("tool_name", "")).strip()
        if not tool_name or tool_name in {"scan_start_info", "subagent_start_info"}:
            return None

        pretty_tool = tool_name.replace("_", " ")
        status = str(latest.get("status", "running"))
        verb = "Running" if status == "running" else "Completed"
        args = latest.get("args", {})
        if not isinstance(args, dict):
            args = {}
        action = str(args.get("action", "")).strip()
        target = str(args.get("url", "") or args.get("target", "") or args.get("selector", "")).strip()

        if action and target:
            return f"{verb} {pretty_tool}: {action} {target}"
        if action:
            return f"{verb} {pretty_tool}: {action}"
        return f"{verb} {pretty_tool}"

    def _truncate_snippet(self, snippet: str, max_len: int = 110) -> str:
        normalized = " ".join(snippet.split())
        if len(normalized) <= max_len:
            return normalized
        return normalized[: max_len - 1] + "…"

    def _get_agent_live_snippet(self, agent_id: str, status: str) -> str:
        if status == "waiting":
            return "Waiting for user input to continue."
        if status in {"completed", "stopped"}:
            return "Agent finished execution."
        if status in {"failed", "llm_failed", "error", "sandbox_failed"}:
            return "Agent failed. Review logs and retry."
        if agent_id in self.tracer.compacting_agents:
            return "🔄 Compacting memory..."

        import time as _time

        _done_until = getattr(self, "_compaction_done_until", {})
        if agent_id in _done_until:
            if _time.monotonic() < _done_until[agent_id]:
                return "✓ Memory compacted"
            _done_until.pop(agent_id, None)

        snippet = self._get_agent_snippet(agent_id)
        if snippet:
            return self._truncate_snippet(snippet)
        tool_snippet = self._get_tool_activity_snippet(agent_id)
        if tool_snippet:
            return self._truncate_snippet(tool_snippet)
        return "Initializing scan flow."

    def _build_status_line_text(self, agent_id: str, agent_data: dict[str, Any]) -> Text:
        palette = self._theme_palette()
        status = str(agent_data.get("status", "running"))
        elapsed = self._format_short_duration(self._get_agent_elapsed_seconds(agent_data))
        token_count, cost = self._get_agent_token_snapshot(agent_id)
        snippet = self._get_agent_live_snippet(agent_id, status)

        text = Text()
        if status in {"running", "waiting"}:
            text.append_text(self._build_running_spinner_indicator())
        elif status in {"completed", "stopped"}:
            text.append("[ok] ", style=self._marker_style("ok"))
        elif status in {"failed", "llm_failed", "error", "sandbox_failed"}:
            text.append("[err] ", style=self._marker_style("err"))
        else:
            text.append("[run] ", style=self._marker_style("run"))

        dim_style = f"dim {str(palette.get('muted', '#9ca3af'))}"
        info_style = str(palette.get("info", "#60a5fa"))
        text.append(elapsed, style=dim_style)
        text.append(" · ", style=dim_style)
        text.append(f"{format_token_count(token_count)} tok", style=dim_style)
        if cost > 0:
            text.append(" · ", style=dim_style)
            text.append(f"${cost:.2f}", style=dim_style)

        # Streaming tok/s and elapsed time
        streaming_start_time = getattr(self, "_streaming_start_time", {})
        if agent_id in streaming_start_time:
            stream_elapsed = time.monotonic() - streaming_start_time[agent_id]
            if stream_elapsed >= 0.5:
                stream_content = self.tracer.get_streaming_content(agent_id) or ""
                # Estimate tokens from streaming content (~4 chars per token)
                estimated_tokens = max(1, len(stream_content) // 4)
                tps = estimated_tokens / stream_elapsed
                text.append(" │ ", style=dim_style)
                text.append(f"{stream_elapsed:.0f}s", style=f"dim {info_style}")
                text.append(" │ ", style=dim_style)
                text.append(f"~{tps:.0f} tok/s", style=f"dim {info_style}")

        text.append(" · ", style=dim_style)
        text.append(snippet, style=str(palette.get("text", "#e8d5d5")))
        return text

    def _build_global_status_snapshot_text(self) -> Text:
        if not self.tracer.agents:
            return Text("Waiting for agents to connect...", style="dim")

        agent_id = self.selected_agent_id
        if not agent_id or agent_id not in self.tracer.agents:
            agent_id = self._get_root_agent_id() or next(iter(self.tracer.agents))
        agent_data = self.tracer.agents.get(agent_id, {})
        status_line = self._build_status_line_text(agent_id, agent_data)

        prefix = Text()
        prefix.append("Live Status  ", style=f"bold {self._marker_color('run')}")
        prefix.append(f"{agent_data.get('name', agent_id)}", style="bold")
        prefix.append("\n", style="dim")
        prefix.append_text(status_line)
        return prefix

    def _get_enriched_vulnerabilities(self) -> list[dict[str, Any]]:
        vulnerabilities = list(getattr(self.tracer, "vulnerability_reports", []))
        if not vulnerabilities:
            return []

        severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        enriched: list[dict[str, Any]] = []
        for vulnerability in vulnerabilities:
            item = dict(vulnerability)
            report_id = str(vulnerability.get("id", ""))
            agent_name = self._get_agent_name_for_vulnerability(report_id)
            if agent_name:
                item["agent_name"] = agent_name
            enriched.append(item)

        enriched.sort(
            key=lambda v: (
                severity_order.get(str(v.get("severity", "")).lower(), 5),
                str(v.get("timestamp", "")),
            )
        )
        return enriched

    def _build_subagent_dashboard(self, root_agent_id: str) -> Any:
        """Build a renderable dashboard showing snippets of all child agents."""
        children = self._get_child_agents(root_agent_id)
        if not children:
            return None

        palette = self._theme_palette()
        header_color = str(palette.get("subagent_header", "#22d3ee"))
        renderables: list[Any] = []

        # Section header
        header = Text()
        header.append("─" * 3, style=f"dim {header_color}")
        header.append("  Subagent Activity  ", style=f"bold {header_color}")
        header.append("─" * 40, style=f"dim {header_color}")
        renderables.append(header)

        spinner = self._running_status_frame()

        status_styles: dict[str, tuple[str, str]] = {
            "running": (f"[{spinner}]", self._marker_color("run")),
            "waiting": ("[warn]", self._marker_color("warn")),
            "completed": ("[ok]", self._marker_color("ok")),
            "failed": ("[err]", self._marker_color("err")),
            "stopped": ("[ok]", self._marker_color("ok")),
            "stopping": ("[warn]", self._marker_color("warn")),
            "llm_failed": ("[err]", self._marker_color("err")),
        }

        for child in children:
            agent_id = child["id"]
            agent_name = child.get("name", "Agent")
            status = child.get("status", "running")
            icon, color = status_styles.get(status, ("[run]", str(palette.get("status_idle", "#947575"))))
            is_active = status in ("running", "waiting")

            card = Text()
            # Agent name line with status icon
            card.append(f"  {icon} ", style=color)
            card.append(agent_name, style=f"bold {color}")

            vuln_count = self._agent_vulnerability_count(agent_id)
            if vuln_count > 0:
                card.append(
                    f"  [warn:{vuln_count}]",
                    style=f"bold {self._marker_color('warn')}",
                )

            snippet = self._get_agent_snippet(agent_id)
            if snippet:
                card.append("\n")
                if is_active:
                    # Shimmer effect for running agents
                    card.append("    ")
                    shimmer = self._shimmer_text(snippet, max_len=90)
                    card.append_text(shimmer)
                else:
                    card.append("    ", style="dim")
                    display = snippet if len(snippet) <= 90 else snippet[:89] + "…"
                    card.append(display, style="dim")
            elif is_active:
                card.append("\n    ", style="dim")
                card.append_text(self._shimmer_text("Initializing…", max_len=90))

            renderables.append(card)

        if len(renderables) <= 1:
            return None

        return Group(*renderables)

    def _has_running_children(self, agent_id: str) -> bool:
        """Check if any child agents are currently running."""
        return any(
            data.get("status") in ("running", "waiting")
            for data in list(self.tracer.agents.values())
            if data.get("parent_id") == agent_id
        )

    def _render_streaming_content(self, content: str, agent_id: str | None = None) -> Any:
        cache_key = agent_id or self.selected_agent_id or ""
        content_len = len(content)

        if cache_key in self._streaming_render_cache:
            cached_len, cached_output = self._streaming_render_cache[cache_key]
            if cached_len == content_len:
                return cached_output

        renderables: list[Any] = []
        segments = parse_streaming_content(content)

        for segment in segments:
            if segment.type == "text":
                text_content = AgentMessageRenderer.render_simple(segment.content)
                if renderables:
                    renderables.append(Text(""))
                renderables.append(text_content)

            elif segment.type == "tool":
                tool_renderable = self._render_streaming_tool(
                    segment.tool_name or "unknown",
                    segment.args or {},
                    segment.is_complete,
                )
                if renderables:
                    renderables.append(Text(""))
                renderables.append(tool_renderable)

        if not renderables:
            result = Text()
        elif len(renderables) == 1:
            result = renderables[0]
        else:
            result = Group(*renderables)

        self._streaming_render_cache[cache_key] = (content_len, result)
        return result

    def _render_streaming_tool(
        self, tool_name: str, args: dict[str, str], is_complete: bool
    ) -> Any:
        palette = self._theme_palette()
        tool_data = {
            "tool_name": tool_name,
            "args": args,
            "status": "completed" if is_complete else "running",
            "result": None,
            "_theme_id": self._theme_id,
            "_theme_tokens": palette,
        }

        # For completed browser actions, try to find the actual result from the tracer
        # so that screenshot previews can render
        if is_complete and tool_name == "browser_action" and self.selected_agent_id:
            tracer_result = self._find_browser_result_from_tracer(args)
            if tracer_result is not None:
                tool_data["result"] = tracer_result

        renderer = get_tool_renderer(tool_name)
        if renderer:
            widget = renderer.render(tool_data)
            return widget.renderable

        return self._render_default_streaming_tool(tool_name, args, is_complete)

    def _find_browser_result_from_tracer(self, args: dict[str, str]) -> dict[str, Any] | None:
        """Find the matching browser action result from the tracer for a streaming tool."""
        action = args.get("action", "")
        url = args.get("url", "")

        # Search backwards (latest first) for a matching completed browser_action
        for tool_data in reversed(list(self.tracer.tool_executions.values())):
            if tool_data.get("tool_name") != "browser_action":
                continue
            if tool_data.get("status") != "completed":
                continue
            if tool_data.get("agent_id") != self.selected_agent_id:
                continue
            t_args = tool_data.get("args", {})
            if t_args.get("action") == action and t_args.get("url", "") == url:
                result = tool_data.get("result")
                if isinstance(result, dict):
                    return result
        return None

    def _render_default_streaming_tool(
        self, tool_name: str, args: dict[str, str], is_complete: bool
    ) -> Text:
        palette = self._theme_palette()
        running_style = self._marker_color("run")
        success_style = self._marker_color("ok")
        muted_style = str(palette.get("muted", "#9ca3af"))
        info_style = str(palette.get("info", "#60a5fa"))
        text = Text()

        if is_complete:
            text.append("[ok] ", style=f"bold {success_style}")
        else:
            frame = self.RUN_STATUS_FRAMES[self._spinner_frame_index % len(self.RUN_STATUS_FRAMES)]
            text.append(f"[{frame}] ", style=f"bold {running_style}")

        text.append("Using tool ", style=f"dim {muted_style}")
        text.append(tool_name, style=f"bold {info_style}")

        if args:
            for key, value in list(args.items())[:3]:
                text.append("\n  ")
                text.append(key, style=f"dim {muted_style}")
                text.append(": ")
                display_value = value if len(value) <= 100 else value[:97] + "..."
                text.append(display_value, style="italic" if not is_complete else None)

        return text

    def _get_status_display_content(
        self, agent_id: str, agent_data: dict[str, Any]
    ) -> tuple[Text | None, Text, bool]:
        palette = self._theme_palette()
        key_style = str(palette.get("keymap_key", "white"))
        text_style = str(palette.get("keymap_text", "dim"))
        status = str(agent_data.get("status", "running"))

        def keymap_styled(keys: list[tuple[str, str]]) -> Text:
            t = Text()
            for i, (key, action) in enumerate(keys):
                if i > 0:
                    t.append(" · ", style=text_style)
                t.append(key, style=key_style)
                t.append(" ", style=text_style)
                t.append(action, style=text_style)
            return t

        line = self._build_status_line_text(agent_id, agent_data)
        if status in {"running", "waiting"}:
            return (
                line,
                keymap_styled([("Ctrl+V", "vulns"), ("Ctrl+H", "health"), ("esc", "stop"), ("ctrl-q", "quit")]),
                True,
            )
        if status in {"failed", "llm_failed", "error", "sandbox_failed"}:
            return (
                line,
                keymap_styled([("send", "retry"), ("Ctrl+V", "vulns"), ("Ctrl+H", "health")]),
                False,
            )
        if status in {"completed", "stopped", "stopping"}:
            return (
                line,
                keymap_styled([("Ctrl+V", "vulns"), ("Ctrl+H", "health"), ("Ctrl+U", "update"), ("ctrl-q", "quit")]),
                False,
            )
        return (line, keymap_styled([("Ctrl+V", "vulns"), ("Ctrl+H", "health"), ("Ctrl+U", "update")]), False)

    def _build_running_spinner_indicator(self) -> Text:
        """Render a stable-width spinner prefix for running-status affordance."""
        palette = self._theme_palette()
        frame = self.RUN_STATUS_FRAMES[self._spinner_frame_index % len(self.RUN_STATUS_FRAMES)]
        running_style = self._marker_color("run")
        muted_style = str(palette.get("muted", "#9ca3af"))
        text = Text()
        text.append("[", style=running_style)
        text.append(frame, style=f"bold {running_style}")
        text.append("]", style=running_style)
        text.append(" ", style=muted_style)
        return text

    def _update_streaming_timing(self) -> None:
        """Track streaming start time and baseline output tokens per agent."""
        active_streaming: set[str] = set()
        for agent_id in list(self.tracer.agents):
            content = self.tracer.get_streaming_content(agent_id)
            if content and content.strip():
                active_streaming.add(agent_id)
                if agent_id not in self._streaming_start_time:
                    self._streaming_start_time[agent_id] = time.monotonic()
                    _, _ = self._get_agent_token_snapshot(agent_id)
                    by_agent = self.tracer.get_total_llm_stats().get("by_agent", {})
                    agent_stats = by_agent.get(str(agent_id), {})
                    self._streaming_start_output_tokens[agent_id] = int(
                        agent_stats.get("output_tokens", 0)
                    )
        # Clear timing for agents that stopped streaming
        for agent_id in list(self._streaming_start_time):
            if agent_id not in active_streaming:
                self._streaming_start_time.pop(agent_id, None)
                self._streaming_start_output_tokens.pop(agent_id, None)

    def _update_agent_status_display(self) -> None:
        try:
            status_display = self.query_one("#agent_status_display", Horizontal)
            status_text = self.query_one("#status_text", Static)
            keymap_indicator = self.query_one("#keymap_indicator", Static)
        except (ValueError, Exception):
            return

        widgets = [status_display, status_text, keymap_indicator]
        if not all(self._is_widget_safe(w) for w in widgets):
            return

        if not self.selected_agent_id:
            self._safe_widget_operation(status_display.add_class, "hidden")
            return

        try:
            agent_data = self.tracer.agents[self.selected_agent_id]
            content, keymap, should_animate = self._get_status_display_content(
                self.selected_agent_id, agent_data
            )

            if not content:
                self._safe_widget_operation(status_display.add_class, "hidden")
                return

            self._safe_widget_operation(status_text.update, content)
            self._safe_widget_operation(keymap_indicator.update, keymap)
            self._safe_widget_operation(status_display.remove_class, "hidden")

            if should_animate:
                self._start_dot_animation()

        except (KeyError, Exception):
            self._safe_widget_operation(status_display.add_class, "hidden")

    def _update_stats_display(self) -> None:
        try:
            stats_display = self.query_one("#stats_display", Static)
        except (ValueError, Exception):
            return

        if not self._is_widget_safe(stats_display):
            return

        self._stats_spinner_frame += 1
        scan_done = self._scan_completed.is_set()
        scan_failed = self._scan_failed.is_set()

        # Detect agent-level failures even if the scan thread is still alive
        # (e.g. LLM 400 error caught inside the agent loop)
        if not scan_failed and not scan_done and self.tracer and self.tracer.agents:
            _FAIL_STATUSES = {"failed", "llm_failed"}
            _DONE_STATUSES = _FAIL_STATUSES | {"completed", "stopped"}
            all_agents_done = all(
                a.get("status") in _DONE_STATUSES
                for a in list(self.tracer.agents.values())
            )
            any_failed = any(
                a.get("status") in _FAIL_STATUSES
                for a in list(self.tracer.agents.values())
            )
            if all_agents_done and any_failed:
                scan_failed = True
                self._scan_failed.set()
                if not self.tracer.end_time:
                    from datetime import datetime, timezone
                    self.tracer.end_time = datetime.now(timezone.utc).isoformat()

        stats_content = Text()

        stats_text = build_tui_stats_text(
            self.tracer,
            self.agent_config,
            scan_completed=scan_done,
            scan_failed=scan_failed,
            spinner_frame=self._stats_spinner_frame,
            theme_tokens=self._theme_palette(),
        )
        if stats_text:
            stats_content.append(stats_text)

        version = get_package_version()
        stats_content.append(f"\nv{version}", style="dim white")

        from rich.panel import Panel

        stats_panel = Panel(
            stats_content,
            title="Session Stats",
            title_align="left",
            border_style="#ef4444" if scan_failed else "#22c55e" if scan_done else "#22d3ee",
            padding=(0, 1),
        )

        self._safe_widget_operation(stats_display.update, stats_panel)

    def _update_vulnerabilities_panel(self) -> None:
        """Update the vulnerabilities panel with current vulnerability data."""
        try:
            vuln_panel = self.query_one("#vulnerabilities_panel", VulnerabilitiesPanel)
        except (ValueError, Exception):
            return

        if not self._is_widget_safe(vuln_panel):
            return

        vulnerabilities = self._get_enriched_vulnerabilities()
        if not vulnerabilities:
            vuln_panel.update_vulnerabilities([])
            return

        vuln_panel.update_vulnerabilities(vulnerabilities)

    def _get_agent_name_for_vulnerability(self, report_id: str) -> str | None:
        """Find the agent name that created a vulnerability report."""
        for _exec_id, tool_data in list(self.tracer.tool_executions.items()):
            if tool_data.get("tool_name") == "create_vulnerability_report":
                result = tool_data.get("result", {})
                if isinstance(result, dict) and result.get("report_id") == report_id:
                    agent_id = tool_data.get("agent_id")
                    if agent_id and agent_id in self.tracer.agents:
                        name: str = self.tracer.agents[agent_id].get("name", "Unknown Agent")
                        return name
        return None

    def _get_sweep_animation(self, color_palette: list[str]) -> Text:
        text = Text()
        num_squares = self._sweep_num_squares
        num_colors = len(color_palette)

        offset = num_colors - 1
        max_pos = (num_squares - 1) + offset
        total_range = max_pos + offset
        cycle_length = total_range * 2
        frame_in_cycle = self._spinner_frame_index % cycle_length

        wave_pos = total_range - abs(total_range - frame_in_cycle)
        sweep_pos = wave_pos - offset

        dot_color = self._sweep_dot_color

        for i in range(num_squares):
            dist = abs(i - sweep_pos)
            color_idx = max(0, num_colors - 1 - dist)

            if color_idx == 0:
                text.append("·", style=Style(color=dot_color))
            else:
                color = color_palette[color_idx]
                text.append("▪", style=Style(color=color))

        text.append(" ")
        return text

    def _get_animated_verb_text(self, agent_id: str, verb: str) -> Text:  # noqa: ARG002
        palette = self._theme_palette()
        verb_primary = str(palette.get("verb_primary", "white"))
        verb_secondary = str(palette.get("verb_secondary", "dim"))
        text = Text()
        sweep = self._get_sweep_animation(self._sweep_colors)
        text.append_text(sweep)
        parts = verb.split(" ", 1)
        text.append(parts[0], style=verb_primary)
        if len(parts) > 1:
            text.append(" ", style=verb_secondary)
            text.append(parts[1], style=verb_secondary)
        return text

    def _start_dot_animation(self) -> None:
        if self._dot_animation_timer is None:
            self._dot_animation_timer = self.set_interval(0.06, self._animate_dots)

    def _stop_dot_animation(self) -> None:
        if self._dot_animation_timer is not None:
            self._dot_animation_timer.stop()
            self._dot_animation_timer = None

    def _animate_dots(self) -> None:
        has_active_agents = False

        if self.selected_agent_id and self.selected_agent_id in self.tracer.agents:
            agent_data = self.tracer.agents[self.selected_agent_id]
            status = agent_data.get("status", "running")
            if status in ["running", "waiting"]:
                has_active_agents = True
                num_colors = len(self._sweep_colors)
                offset = num_colors - 1
                max_pos = (self._sweep_num_squares - 1) + offset
                total_range = max_pos + offset
                cycle_length = total_range * 2
                self._spinner_frame_index = (self._spinner_frame_index + 1) % cycle_length
                self._update_agent_status_display()

        if not has_active_agents:
            has_active_agents = any(
                agent_data.get("status", "running") in ["running", "waiting"]
                for agent_data in list(self.tracer.agents.values())
            )

        if not has_active_agents:
            self._stop_dot_animation()
            self._spinner_frame_index = 0

    def _agent_has_real_activity(self, agent_id: str) -> bool:
        initial_tools = {"scan_start_info", "subagent_start_info"}

        for _exec_id, tool_data in list(self.tracer.tool_executions.items()):
            if tool_data.get("agent_id") == agent_id:
                tool_name = tool_data.get("tool_name", "")
                if tool_name not in initial_tools:
                    return True

        streaming = self.tracer.get_streaming_content(agent_id)
        return bool(streaming and streaming.strip())

    def _agent_vulnerability_count(self, agent_id: str) -> int:
        count = 0
        for _exec_id, tool_data in list(self.tracer.tool_executions.items()):
            if tool_data.get("agent_id") == agent_id:
                tool_name = tool_data.get("tool_name", "")
                if tool_name == "create_vulnerability_report":
                    status = tool_data.get("status", "")
                    if status == "completed":
                        result = tool_data.get("result", {})
                        if isinstance(result, dict) and result.get("success"):
                            count += 1
        return count

    def _agent_last_activity_age_seconds(self, agent_id: str, agent_data: dict[str, Any]) -> float:
        timestamps: list[datetime] = []

        updated_at = self._parse_iso(str(agent_data.get("updated_at") or ""))
        if updated_at:
            timestamps.append(updated_at)

        for message in getattr(self.tracer, "chat_messages", []):
            if message.get("agent_id") != agent_id:
                continue
            msg_ts = self._parse_iso(str(message.get("timestamp") or ""))
            if msg_ts:
                timestamps.append(msg_ts)

        for tool_data in getattr(self.tracer, "tool_executions", {}).values():
            if tool_data.get("agent_id") != agent_id:
                continue
            started = self._parse_iso(str(tool_data.get("timestamp") or ""))
            completed = self._parse_iso(str(tool_data.get("completed_at") or ""))
            if started:
                timestamps.append(started)
            if completed:
                timestamps.append(completed)

        get_streaming = getattr(self.tracer, "get_streaming_content", None)
        if callable(get_streaming) and get_streaming(agent_id):
            timestamps.append(datetime.now(UTC))

        if not timestamps:
            created = self._parse_iso(str(agent_data.get("created_at") or ""))
            if created:
                timestamps.append(created)

        if not timestamps:
            return 0.0
        latest = max(timestamps)
        return max(0.0, (datetime.now(UTC) - latest).total_seconds())

    def _agent_error_streak(self, agent_id: str) -> int:
        tools = [
            tool_data
            for tool_data in self.tracer.tool_executions.values()
            if tool_data.get("agent_id") == agent_id
        ]
        if not tools:
            return 0

        tools.sort(
            key=lambda t: (
                str(t.get("completed_at") or ""),
                str(t.get("timestamp") or ""),
                int(t.get("execution_id") or 0),
            ),
            reverse=True,
        )
        failed_states = {"failed", "error"}
        streak = 0
        for tool in tools:
            status = str(tool.get("status", "")).lower()
            if status in failed_states:
                streak += 1
                continue
            if status in {"completed", "running"}:
                break
        return streak

    def _agent_retry_count(self, agent_id: str) -> int:
        retry_tools = {"llm_error_details", "sandbox_error_details"}
        retries = 0
        for tool_data in self.tracer.tool_executions.values():
            if tool_data.get("agent_id") != agent_id:
                continue
            tool_name = str(tool_data.get("tool_name", ""))
            status = str(tool_data.get("status", ""))
            if tool_name in retry_tools or status.lower() in {"failed", "error"}:
                retries += 1
        agent_status = str(self.tracer.agents.get(agent_id, {}).get("status", "")).lower()
        if agent_status in {"failed", "llm_failed", "error", "sandbox_failed"}:
            retries += 1
        return retries

    def _agent_risk_tier(
        self, status: str, age_seconds: float, error_streak: int, retry_count: int
    ) -> tuple[str, int]:
        score = 0
        failed_statuses = {"failed", "llm_failed", "error", "sandbox_failed"}
        if status in failed_statuses:
            score += 80
        elif status in {"running", "waiting"}:
            score += 20

        if age_seconds >= 240:
            score += 40
        elif age_seconds >= 120:
            score += 25
        elif age_seconds >= 60:
            score += 10

        score += min(error_streak * 15, 45)
        score += min(retry_count * 5, 25)

        if status in {"running", "waiting"} and age_seconds >= 120 and error_streak >= 2:
            score += 25

        if score >= 70:
            return ("high", score)
        if score >= 40:
            return ("medium", score)
        return ("low", score)

    def _get_agent_health_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for agent_id, agent_data in self.tracer.agents.items():
            status = str(agent_data.get("status", "running")).lower()
            age_seconds = self._agent_last_activity_age_seconds(agent_id, agent_data)
            error_streak = self._agent_error_streak(agent_id)
            retry_count = self._agent_retry_count(agent_id)
            risk, risk_score = self._agent_risk_tier(status, age_seconds, error_streak, retry_count)

            rows.append(
                {
                    "agent_id": agent_id,
                    "name": str(agent_data.get("name", agent_id)),
                    "status": status,
                    "last_output_age": self._format_short_duration(age_seconds),
                    "error_streak": error_streak,
                    "retry_count": retry_count,
                    "risk": risk,
                    "risk_score": risk_score,
                    "snippet": self._get_agent_live_snippet(agent_id, status),
                }
            )

        rows.sort(key=lambda item: (-int(item["risk_score"]), str(item["name"]).lower()))
        return rows

    def _gather_agent_events(self, agent_id: str) -> list[dict[str, Any]]:
        chat_events = [
            {
                "type": "chat",
                "timestamp": msg["timestamp"],
                "id": f"chat_{msg['message_id']}",
                "data": msg,
            }
            for msg in list(self.tracer.chat_messages)
            if msg.get("agent_id") == agent_id
        ]

        tool_events = [
            {
                "type": "tool",
                "timestamp": tool_data["timestamp"],
                "id": f"tool_{exec_id}",
                "data": tool_data,
            }
            for exec_id, tool_data in list(self.tracer.tool_executions.items())
            if tool_data.get("agent_id") == agent_id
        ]

        events = chat_events + tool_events
        events.sort(key=lambda e: (e["timestamp"], e["id"]))
        return events

    def watch_selected_agent_id(self, _agent_id: str | None) -> None:
        if len(self.screen_stack) > 1 or self.show_splash:
            return

        if not self.is_mounted:
            return

        self._displayed_events.clear()
        self._streaming_render_cache.clear()
        self._last_streaming_len.clear()
        self._streaming_start_time.clear()
        self._streaming_start_output_tokens.clear()

        self.call_later(self._update_chat_view)
        self._update_agent_status_display()

    def _start_scan_thread(self) -> None:
        def scan_target() -> None:
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                try:
                    agent = EspritAgent(self.agent_config)

                    if not self._scan_stop_event.is_set():
                        loop.run_until_complete(agent.execute_scan(self.scan_config))

                except (KeyboardInterrupt, asyncio.CancelledError):
                    logging.info("Scan interrupted by user")
                    self._scan_failed.set()
                except (ConnectionError, TimeoutError):
                    logging.exception("Network error during scan")
                    self._scan_failed.set()
                except RuntimeError:
                    logging.exception("Runtime error during scan")
                    self._scan_failed.set()
                except Exception:
                    logging.exception("Unexpected error during scan")
                    self._scan_failed.set()
                finally:
                    # Freeze elapsed time by setting end_time on the tracer
                    if self.tracer and not self.tracer.end_time:
                        from datetime import datetime, timezone
                        self.tracer.end_time = datetime.now(timezone.utc).isoformat()
                    loop.close()
                    self._scan_completed.set()

            except Exception:
                logging.exception("Error setting up scan thread")
                self._scan_failed.set()
                if self.tracer and not self.tracer.end_time:
                    from datetime import datetime, timezone
                    self.tracer.end_time = datetime.now(timezone.utc).isoformat()
                self._scan_completed.set()

        self._scan_thread = threading.Thread(target=scan_target, daemon=True)
        self._scan_thread.start()

    def _add_agent_node(self, agent_data: dict[str, Any]) -> None:
        if len(self.screen_stack) > 1 or self.show_splash:
            return

        if not self.is_mounted:
            return

        agent_id = agent_data["id"]
        parent_id = agent_data.get("parent_id")
        status = agent_data.get("status", "running")

        try:
            agents_tree = self.query_one("#agents_tree", Tree)
        except (ValueError, Exception):
            return

        agent_name_raw = agent_data.get("name", "Agent")
        status_icon = self._agent_status_marker(status)
        vuln_count = self._agent_vulnerability_count(agent_id)
        vuln_indicator = f" ({vuln_count})" if vuln_count > 0 else ""
        agent_name = f"{status_icon} {agent_name_raw}{vuln_indicator}"

        try:
            if parent_id and parent_id in self.agent_nodes:
                parent_node = self.agent_nodes[parent_id]
                agent_node = parent_node.add(
                    agent_name,
                    data={"agent_id": agent_id},
                )
                parent_node.allow_expand = True
            else:
                agent_node = agents_tree.root.add(
                    agent_name,
                    data={"agent_id": agent_id},
                )

            agent_node.allow_expand = False
            agent_node.expand()
            self.agent_nodes[agent_id] = agent_node

            if len(self.agent_nodes) == 1:
                agents_tree.select_node(agent_node)
                self.selected_agent_id = agent_id

            self._reorganize_orphaned_agents(agent_id)
        except (AttributeError, ValueError, RuntimeError) as e:
            logging.warning(f"Failed to add agent node {agent_id}: {e}")

    def _expand_new_agent_nodes(self) -> None:
        if len(self.screen_stack) > 1 or self.show_splash:
            return

        if not self.is_mounted:
            return

    def _expand_all_agent_nodes(self) -> None:
        if len(self.screen_stack) > 1 or self.show_splash:
            return

        if not self.is_mounted:
            return

        try:
            agents_tree = self.query_one("#agents_tree", Tree)
            self._expand_node_recursively(agents_tree.root)
        except (ValueError, Exception):
            logging.debug("Tree not ready for expanding nodes")

    def _expand_node_recursively(self, node: TreeNode) -> None:
        if not node.is_expanded:
            node.expand()
        for child in node.children:
            self._expand_node_recursively(child)

    def _copy_node_under(self, node_to_copy: TreeNode, new_parent: TreeNode) -> None:
        agent_id = node_to_copy.data["agent_id"]
        agent_data = self.tracer.agents.get(agent_id, {})
        agent_name_raw = agent_data.get("name", "Agent")
        status = agent_data.get("status", "running")
        status_icon = self._agent_status_marker(status)
        vuln_count = self._agent_vulnerability_count(agent_id)
        vuln_indicator = f" ({vuln_count})" if vuln_count > 0 else ""
        agent_name = f"{status_icon} {agent_name_raw}{vuln_indicator}"

        new_node = new_parent.add(
            agent_name,
            data=node_to_copy.data,
        )
        new_node.allow_expand = node_to_copy.allow_expand

        self.agent_nodes[agent_id] = new_node

        for child in node_to_copy.children:
            self._copy_node_under(child, new_node)

        if node_to_copy.is_expanded:
            new_node.expand()

    def _reorganize_orphaned_agents(self, new_parent_id: str) -> None:
        agents_to_move = []

        for agent_id, agent_data in list(self.tracer.agents.items()):
            if (
                agent_data.get("parent_id") == new_parent_id
                and agent_id in self.agent_nodes
                and agent_id != new_parent_id
            ):
                agents_to_move.append(agent_id)

        if not agents_to_move:
            return

        parent_node = self.agent_nodes[new_parent_id]

        for child_agent_id in agents_to_move:
            if child_agent_id in self.agent_nodes:
                old_node = self.agent_nodes[child_agent_id]

                if old_node.parent is parent_node:
                    continue

                self._copy_node_under(old_node, parent_node)

                old_node.remove()

        parent_node.allow_expand = True
        parent_node.expand()

    def _render_chat_content(self, msg_data: dict[str, Any]) -> Any:
        role = msg_data.get("role")
        content = msg_data.get("content", "")
        metadata = msg_data.get("metadata", {})

        if not content:
            return None

        if role == "user":
            return UserMessageRenderer.render_simple(content)

        if metadata.get("interrupted"):
            palette = self._theme_palette()
            streaming_result = self._render_streaming_content(content)
            interrupted_text = Text()
            interrupted_text.append("\n")
            interrupted_text.append("[warn] ", style=self._marker_style("warn"))
            interrupted_text.append(
                "Interrupted by user",
                style=f"dim {str(palette.get('warning', '#f59e0b'))}",
            )
            return Group(streaming_result, interrupted_text)

        return AgentMessageRenderer.render_simple(content)

    def _render_tool_content_simple(self, tool_data: dict[str, Any]) -> Any:
        tool_name = tool_data.get("tool_name", "Unknown Tool")
        args = tool_data.get("args", {})
        status = tool_data.get("status", "unknown")
        result = tool_data.get("result")

        renderer = get_tool_renderer(tool_name)

        if renderer:
            renderer_payload = dict(tool_data)
            renderer_payload["_theme_id"] = self._theme_id
            renderer_payload["_theme_tokens"] = self._theme_palette()
            widget = renderer.render(renderer_payload)
            return widget.renderable

        text = Text()
        palette = self._theme_palette()
        muted_style = str(palette.get("muted", "#9ca3af"))
        info_style = str(palette.get("info", "#60a5fa"))
        success_style = self._marker_color("ok")
        warning_style = self._marker_color("run")
        error_style = self._marker_color("err")

        if tool_name in ("llm_error_details", "sandbox_error_details"):
            return self._render_error_details(text, tool_name, args)

        text.append("[run] ", style=f"bold {warning_style}")
        text.append("Using tool ", style=f"dim {muted_style}")
        text.append(tool_name, style=f"bold {info_style}")

        status_styles = {
            "running": ("[run]", warning_style),
            "completed": ("[ok]", success_style),
            "failed": ("[err]", error_style),
            "error": ("[err]", error_style),
        }
        icon, style = status_styles.get(status, ("[warn]", self._marker_color("warn")))
        text.append(" ")
        text.append(icon, style=f"bold {style}")

        if args:
            for k, v in list(args.items())[:5]:
                str_v = str(v)
                if len(str_v) > 500:
                    str_v = str_v[:497] + "..."
                text.append("\n  ")
                text.append(k, style=f"dim {muted_style}")
                text.append(": ")
                text.append(str_v)

        if status in ["completed", "failed", "error"] and result:
            result_str = str(result)
            if len(result_str) > 1000:
                result_str = result_str[:997] + "..."
            text.append("\n")
            text.append("Result: ", style="bold")
            text.append(result_str)

        return text

    def _render_error_details(self, text: Any, tool_name: str, args: dict[str, Any]) -> Any:
        palette = self._theme_palette()
        error_style = str(palette.get("status_failed", "#ef4444"))
        muted_style = str(palette.get("muted", "#9ca3af"))
        if tool_name == "llm_error_details":
            text.append("[err] ", style=self._marker_style("err"))
            text.append("LLM request failed", style=f"bold {error_style}")
        else:
            text.append("[err] ", style=self._marker_style("err"))
            text.append("Sandbox initialization failed", style=f"bold {error_style}")
            if args.get("error"):
                text.append(f"\n{args['error']}", style=f"bold {error_style}")
        if args.get("details"):
            details = str(args["details"])
            if len(details) > 1000:
                details = details[:997] + "..."
            text.append("\nDetails: ", style=f"dim {muted_style}")
            text.append(details)
        return text

    @on(Tree.NodeHighlighted)  # type: ignore[misc]
    def handle_tree_highlight(self, event: Tree.NodeHighlighted) -> None:
        if len(self.screen_stack) > 1 or self.show_splash:
            return

        if not self.is_mounted:
            return

        node = event.node

        try:
            agents_tree = self.query_one("#agents_tree", Tree)
        except (ValueError, Exception):
            return

        if self.focused == agents_tree and node.data:
            agent_id = node.data.get("agent_id")
            if agent_id:
                self.selected_agent_id = agent_id

    @on(Tree.NodeSelected)  # type: ignore[misc]
    def handle_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        if len(self.screen_stack) > 1 or self.show_splash:
            return

        if not self.is_mounted:
            return

        node = event.node

        if node.allow_expand:
            if node.is_expanded:
                node.collapse()
            else:
                node.expand()

    def _send_user_message(self, message: str) -> None:
        if not self.selected_agent_id:
            return
        self._send_user_message_to_agent(self.selected_agent_id, message, interrupt_if_streaming=True)

        self._displayed_events.clear()
        self._update_chat_view()

        self.call_after_refresh(self._focus_chat_input)

    def _send_user_message_to_agent(
        self,
        agent_id: str,
        message: str,
        *,
        interrupt_if_streaming: bool,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        if interrupt_if_streaming and self.tracer:
            streaming_content = self.tracer.get_streaming_content(agent_id)
            if streaming_content and streaming_content.strip():
                self.tracer.clear_streaming_content(agent_id)
                self.tracer.interrupted_content[agent_id] = streaming_content
                self.tracer.log_chat_message(
                    content=streaming_content,
                    role="assistant",
                    agent_id=agent_id,
                    metadata={"interrupted": True},
                )

        try:
            from esprit.tools.agents_graph.agents_graph_actions import _agent_instances

            if agent_id in _agent_instances:
                agent_instance = _agent_instances[agent_id]
                if interrupt_if_streaming and hasattr(agent_instance, "cancel_current_execution"):
                    agent_instance.cancel_current_execution()
        except (ImportError, AttributeError, KeyError):
            pass

        if self.tracer:
            self.tracer.log_chat_message(
                content=message,
                role="user",
                agent_id=agent_id,
                metadata=metadata or {},
            )

        try:
            from esprit.tools.agents_graph.agents_graph_actions import send_user_message_to_agent

            send_user_message_to_agent(agent_id, message)
        except (ImportError, AttributeError) as e:
            logging.warning(f"Failed to send message to agent {agent_id}: {e}")
            return False
        else:
            return True

    def retry_agent(self, agent_id: str) -> bool:
        retry_message = (
            "Please continue your current task. Prioritize high-severity vulnerabilities first "
            "and summarize your next step."
        )
        success = self._send_user_message_to_agent(
            agent_id,
            retry_message,
            interrupt_if_streaming=True,
            metadata={"retry": True},
        )
        if success and agent_id == self.selected_agent_id:
            self._displayed_events.clear()
            self._update_chat_view()
        return success

    def _request_stop_agent(self, agent_id: str) -> bool:
        try:
            from esprit.tools.agents_graph.agents_graph_actions import stop_agent

            result = stop_agent(agent_id)
            if result.get("success"):
                logging.info(f"Stop request sent to agent: {result.get('message', 'Unknown')}")
                return True
            logging.warning(f"Failed to stop agent: {result.get('error', 'Unknown error')}")
        except Exception:
            logging.exception(f"Failed to stop agent {agent_id}")
        return False

    def _get_agent_name(self, agent_id: str) -> str:
        try:
            if self.tracer and agent_id in self.tracer.agents:
                agent_name = self.tracer.agents[agent_id].get("name")
                if isinstance(agent_name, str):
                    return agent_name
        except (KeyError, AttributeError) as e:
            logging.warning(f"Could not retrieve agent name for {agent_id}: {e}")
        return "Unknown Agent"

    def action_toggle_help(self) -> None:
        if self.show_splash or not self.is_mounted:
            return

        try:
            self.query_one("#main_container")
        except (ValueError, Exception):
            return

        if isinstance(self.screen, HelpScreen):
            self.pop_screen()
            return

        if len(self.screen_stack) > 1:
            return

        self.push_screen(HelpScreen())

    def action_show_help_overlay(self) -> None:
        if self.show_splash or not self.is_mounted:
            return

        # Don't open when chat input is focused (user might be typing '?')
        try:
            chat_input = self.query_one("#chat_input", ChatTextArea)
            if self.focused == chat_input:
                return
        except (ValueError, Exception):
            pass

        if isinstance(self.screen, HelpOverlay):
            self.pop_screen()
            return

        if len(self.screen_stack) > 1:
            return

        self.push_screen(HelpOverlay())

    def action_clear_chat(self) -> None:
        if self.show_splash or not self.is_mounted:
            return

        if len(self.screen_stack) > 1:
            return

        try:
            chat_display = self.query_one("#chat_display", Static)
            chat_display.update("")
        except (ValueError, Exception):
            return

    def action_show_browser_preview(self) -> None:
        """Show enlarged browser preview for the selected agent."""
        if self.show_splash or not self.is_mounted:
            return

        if len(self.screen_stack) > 1:
            return

        # Don't open when chat input is focused (user might be typing 'b')
        try:
            chat_input = self.query_one("#chat_input", ChatTextArea)
            if self.focused == chat_input:
                return
        except (ValueError, Exception):
            pass

        if not self.selected_agent_id:
            return

        # Find the latest browser screenshot for the selected agent
        screenshot_b64, url = self._get_latest_browser_screenshot(self.selected_agent_id)
        if not screenshot_b64:
            return

        self.push_screen(BrowserPreviewScreen(screenshot_b64, url, agent_id=self.selected_agent_id))

    def action_toggle_vulnerability_overlay(self) -> None:
        if self.show_splash or not self.is_mounted:
            return

        if isinstance(self.screen, VulnerabilityOverlayScreen):
            self.pop_screen()
            return

        if len(self.screen_stack) > 1:
            return

        self.push_screen(VulnerabilityOverlayScreen())

    def action_toggle_agent_health_popup(self) -> None:
        if self.show_splash or not self.is_mounted:
            return

        if isinstance(self.screen, AgentHealthPopupScreen):
            self.pop_screen()
            return

        if len(self.screen_stack) > 1:
            return

        self.push_screen(AgentHealthPopupScreen())

    def action_check_for_updates(self) -> None:
        if self.show_splash or not self.is_mounted:
            return

        if isinstance(self.screen, UpdateScreen):
            self.pop_screen()
            return

        if len(self.screen_stack) > 1:
            return

        self.push_screen(UpdateScreen(checking=True))

    def _get_latest_browser_screenshot(self, agent_id: str) -> tuple[str | None, str]:
        """Find the latest browser screenshot for an agent."""
        latest_exec_id = self.tracer.latest_browser_screenshots.get(agent_id)

        # If we have a tracked latest, try that first
        if latest_exec_id and latest_exec_id in self.tracer.tool_executions:
            tool_data = self.tracer.tool_executions[latest_exec_id]
            result = tool_data.get("result")
            if isinstance(result, dict):
                screenshot = result.get("screenshot")
                if screenshot and isinstance(screenshot, str) and screenshot != "[rendered]":
                    url = result.get("url") or tool_data.get("args", {}).get("url") or ""
                    return screenshot, url

        # Fallback: search all browser actions for this agent
        best_exec_id = -1
        best_screenshot = None
        best_url = ""

        for exec_id, tool_data in list(self.tracer.tool_executions.items()):
            if tool_data.get("tool_name") != "browser_action":
                continue
            if tool_data.get("agent_id") != agent_id:
                continue
            result = tool_data.get("result")
            if not isinstance(result, dict):
                continue
            screenshot = result.get("screenshot")
            if not screenshot or not isinstance(screenshot, str) or screenshot == "[rendered]":
                continue
            if exec_id > best_exec_id:
                best_exec_id = exec_id
                best_screenshot = screenshot
                best_url = result.get("url") or tool_data.get("args", {}).get("url") or ""

        return best_screenshot, best_url

    def action_request_quit(self) -> None:
        if self.show_splash or not self.is_mounted:
            self.action_custom_quit()
            return

        if len(self.screen_stack) > 1:
            return

        try:
            self.query_one("#main_container")
        except (ValueError, Exception):
            self.action_custom_quit()
            return

        self.push_screen(QuitScreen())

    def action_stop_selected_agent(self) -> None:
        if self.show_splash or not self.is_mounted:
            return

        if len(self.screen_stack) > 1:
            self.pop_screen()
            return

        if not self.selected_agent_id:
            return

        agent_name, should_stop = self._validate_agent_for_stopping()
        if not should_stop:
            return

        try:
            self.query_one("#main_container")
        except (ValueError, Exception):
            return

        self.push_screen(StopAgentScreen(agent_name, self.selected_agent_id))

    def _validate_agent_for_stopping(self) -> tuple[str, bool]:
        agent_name = "Unknown Agent"

        try:
            if self.tracer and self.selected_agent_id in self.tracer.agents:
                agent_data = self.tracer.agents[self.selected_agent_id]
                agent_name = agent_data.get("name", "Unknown Agent")

                agent_status = agent_data.get("status", "running")
                if agent_status not in ["running"]:
                    return agent_name, False

                agent_events = self._gather_agent_events(self.selected_agent_id)
                if not agent_events:
                    return agent_name, False

                return agent_name, True

        except (KeyError, AttributeError, ValueError) as e:
            logging.warning(f"Failed to gather agent events: {e}")

        return agent_name, False

    def action_confirm_stop_agent(self, agent_id: str) -> None:
        self.pop_screen()
        self._request_stop_agent(agent_id)

    def action_custom_quit(self) -> None:
        if self._scan_thread and self._scan_thread.is_alive():
            self._scan_stop_event.set()

            self._scan_thread.join(timeout=1.0)

        # Stop GUI server if running
        if self._gui_server is not None:
            try:
                self._gui_server.stop()
            except Exception:  # noqa: BLE001
                pass

        self.tracer.cleanup()

        self.exit()

    def _is_widget_safe(self, widget: Any) -> bool:
        try:
            _ = widget.screen
        except (AttributeError, ValueError, Exception):
            return False
        else:
            return bool(widget.is_mounted)

    def _safe_widget_operation(
        self, operation: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> bool:
        try:
            operation(*args, **kwargs)
        except (AttributeError, ValueError, Exception):
            return False
        else:
            return True

    def _apply_responsive_layout(self, width: int) -> None:
        try:
            left_panel = self.query_one("#left_panel", Vertical)
            right_panel = self.query_one("#right_panel", Vertical)
            chat_area = self.query_one("#chat_area_container", Vertical)
        except (ValueError, Exception):
            return

        if width < self.LEFT_ONLY_LAYOUT_MIN_WIDTH:
            left_panel.add_class("-hidden")
            right_panel.add_class("-hidden")
            chat_area.add_class("-full-width")

            # Show hint about GUI dashboard for small terminals
            if self._gui_server is not None:
                try:
                    keymap = self.query_one("#keymap_indicator", Static)
                    keymap.update(Text("Dashboard: http://localhost:7860", style="dim"))
                except (ValueError, Exception):
                    pass

            return

        chat_area.remove_class("-full-width")
        left_panel.remove_class("-hidden")

        if width < self.THREE_PANE_LAYOUT_MIN_WIDTH:
            right_panel.add_class("-hidden")
        else:
            right_panel.remove_class("-hidden")

    def on_resize(self, event: events.Resize) -> None:
        if self.show_splash or not self.is_mounted:
            return

        self._apply_responsive_layout(event.size.width)


async def run_tui(args: argparse.Namespace, gui_server: Any = None) -> Any:
    """Run esprit in interactive TUI mode with textual."""
    app = EspritTUIApp(args, gui_server=gui_server)
    return await app.run_async()
