import argparse
import asyncio
import atexit
import logging
import signal
import sys
import threading
from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from typing import TYPE_CHECKING, Any, ClassVar


if TYPE_CHECKING:
    from textual.timer import Timer

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
from esprit.interface.streaming_parser import parse_streaming_content
from esprit.interface.tool_components.agent_message_renderer import AgentMessageRenderer
from esprit.interface.tool_components.registry import get_tool_renderer
from esprit.interface.tool_components.user_message_renderer import UserMessageRenderer
from esprit.interface.utils import build_tui_stats_text, _ACTIVITY_SPINNER
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
        target_lines = min(max(1, line_count), 8)

        new_height = target_lines + 2

        if self.parent.styles.height != new_height:
            self.parent.styles.height = new_height
            self.scroll_cursor_visible()


class SplashScreen(Static):  # type: ignore[misc]
    PRIMARY_GREEN = "#32d9ff"
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

        return Panel.fit(content, border_style="#0b7f95", padding=(1, 4))

    def _build_welcome_text(self) -> Text:
        text = Text("Ghost shell online: ", style=Style(color="white", bold=True))
        text.append("Esprit", style=Style(color="#67e8f9", bold=True))
        return text

    def _build_version_text(self) -> Text:
        return Text(f"v{self._version}", style=Style(color="#9ca3af", dim=True))

    def _build_tagline_text(self) -> Text:
        return Text("Open-source AI hackers for your apps", style=Style(color="#94a3b8", dim=True))

    def _build_wordmark_text(self, phase: int) -> Text:
        palette = ("#7dd3fc", "#38bdf8", "#22d3ee", "#06b6d4", "#0891b2")
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
                    style = Style(color="#ecfeff", bold=True)
                elif dist <= 3:
                    style = Style(color="#bae6fd", bold=True)
                else:
                    style = Style(color=base, bold=True)
                row_text.append(char, style=style)

            wordmark.append_text(row_text)
            if row_index < len(self.WORDMARK) - 1:
                wordmark.append("\n")
        return wordmark

    def _build_ghost_text(self, phase: int) -> Text:
        frame = self.GHOST_FRAMES[phase % len(self.GHOST_FRAMES)]
        ghost = Text()

        for line_index, line in enumerate(frame):
            line_text = Text()
            i = 0
            while i < len(line):
                chunk = line[i : i + 2]
                if chunk == "[]":
                    line_text.append("██", style=Style(color="#22d3ee", bold=True))
                    i += 2
                    continue

                char = line[i]
                if char == "*":
                    sparkle = "#a5f3fc" if (phase + line_index + i) % 2 == 0 else "#38bdf8"
                    line_text.append(char, style=Style(color=sparkle, bold=True))
                else:
                    line_text.append(char)
                i += 1

            ghost.append_text(line_text)
            if line_index < len(frame) - 1:
                ghost.append("\n")

        return ghost

    def _build_start_line_text(self, phase: int) -> Text:
        full_text = "Booting ghost runtime"
        text_len = len(full_text)

        shine_pos = phase % (text_len + 8)

        text = Text()
        for i, char in enumerate(full_text):
            dist = abs(i - shine_pos)

            if dist <= 1:
                style = Style(color="bright_white", bold=True)
            elif dist <= 3:
                style = Style(color="white", bold=True)
            elif dist <= 5:
                style = Style(color="#a3a3a3")
            else:
                style = Style(color="#525252")

            text.append(char, style=style)

        return text


class HelpScreen(ModalScreen):  # type: ignore[misc]
    def compose(self) -> ComposeResult:
        yield Grid(
            Label("Esprit Help", id="help_title"),
            Label(
                "F1        Help\nCtrl+Q/C  Quit\nESC       Stop Agent\n"
                "Enter     Send message to agent\nTab       Switch panels\n↑/↓       Navigate tree\n"
                "b         Browser preview",
                id="help_content",
            ),
            id="dialog",
        )

    def on_key(self, _event: events.Key) -> None:
        self.app.pop_screen()


class StopAgentScreen(ModalScreen):  # type: ignore[misc]
    def __init__(self, agent_name: str, agent_id: str):
        super().__init__()
        self.agent_name = agent_name
        self.agent_id = agent_id

    def compose(self) -> ComposeResult:
        yield Grid(
            Label(f"🛑 Stop '{self.agent_name}'?", id="stop_agent_title"),
            Grid(
                Button("Yes", variant="error", id="stop_agent"),
                Button("No", variant="default", id="cancel_stop"),
                id="stop_agent_buttons",
            ),
            id="stop_agent_dialog",
        )

    def on_mount(self) -> None:
        cancel_button = self.query_one("#cancel_stop", Button)
        cancel_button.focus()

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
        text = Text()

        text.append("🐞 ")
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
            self.set_timer(1.5, lambda: setattr(copy_button, "label", "Copy"))
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
            pass
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
        cancel_button = self.query_one("#cancel", Button)
        cancel_button.focus()

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


class EspritTUIApp(App):  # type: ignore[misc]
    CSS_PATH = "assets/tui_styles.tcss"

    LEFT_ONLY_LAYOUT_MIN_WIDTH = 120
    THREE_PANE_LAYOUT_MIN_WIDTH = 170
    MINI_GHOST_EYE_FRAMES: ClassVar[tuple[str, ...]] = ("o o", "O O", "o o", "- -")
    MINI_GHOST_TAIL_FRAMES: ClassVar[tuple[str, ...]] = ("~", "^", "~", "-")

    selected_agent_id: reactive[str | None] = reactive(default=None)
    show_splash: reactive[bool] = reactive(default=True)

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("f1", "toggle_help", "Help", priority=True),
        Binding("ctrl+q", "request_quit", "Quit", priority=True),
        Binding("ctrl+c", "request_quit", "Quit", priority=True),
        Binding("escape", "stop_selected_agent", "Stop Agent", priority=True),
        Binding("b", "show_browser_preview", "Browser Preview", priority=False),
    ]

    def __init__(self, args: argparse.Namespace, gui_server: _GUIServerType = None):
        super().__init__()
        self.args = args
        self._gui_server = gui_server
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

        self._scan_thread: threading.Thread | None = None
        self._scan_stop_event = threading.Event()
        self._scan_completed = threading.Event()
        self._scan_failed = threading.Event()
        self._stats_spinner_frame: int = 0

        self._spinner_frame_index: int = 0  # Current animation frame index
        self._sweep_num_squares: int = 6  # Number of squares in sweep animation
        self._sweep_colors: list[str] = [
            "#000000",  # Dimmest (shows dot)
            "#001014",
            "#03232d",
            "#0b3a47",
            "#0f5b6f",
            "#0f7993",
            "#22d3ee",
            "#67e8f9",  # Brightest
        ]
        self._compact_sweep_colors: list[str] = [
            "#000000",
            "#1a0f00",
            "#3d2400",
            "#5c3a00",
            "#7a5200",
            "#a36e00",
            "#e09b00",
            "#fbbf24",  # Amber brightest
        ]
        self._dot_animation_timer: Any | None = None

        self._setup_cleanup_handlers()

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
        }

        if getattr(args, "local_sources", None):
            config["local_sources"] = args.local_sources

        return config

    def _setup_cleanup_handlers(self) -> None:
        def cleanup_on_exit() -> None:
            from esprit.runtime import cleanup_runtime

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

            left_panel_header = Static("Subagents", id="left_panel_header")

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

            right_panel_header = Static("Vulnerabilities", id="right_panel_header")

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

    def _update_agent_node(self, agent_id: str, agent_data: dict[str, Any]) -> bool:
        if agent_id not in self.agent_nodes:
            return False

        try:
            agent_node = self.agent_nodes[agent_id]
            agent_name_raw = agent_data.get("name", "Agent")
            status = agent_data.get("status", "running")

            status_indicators = {
                "running": _ACTIVITY_SPINNER[self._stats_spinner_frame % len(_ACTIVITY_SPINNER)],
                "waiting": "⏸",
                "completed": "✓",
                "failed": "✗",
                "stopped": "■",
                "stopping": "○",
                "llm_failed": "✗",
            }

            status_icon = status_indicators.get(status, "○")
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
            is_at_bottom = chat_history.scroll_y >= chat_history.max_scroll_y
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
        text = Text()
        # Animated spinner using the stats spinner frame
        frames = ["◐", "◓", "◑", "◒"]
        frame = frames[self._stats_spinner_frame % len(frames)]
        text.append(f" {frame} ", style="#fbbf24")
        text.append("Compacting memory", style="#d97706 bold")
        text.append("  ·  ", style="dim")
        text.append("summarizing older messages to free context", style="dim")
        return text

    # ------------------------------------------------------------------
    # Shimmer text + subagent dashboard
    # ------------------------------------------------------------------

    _SHIMMER_COLORS: ClassVar[list[str]] = [
        "#3f3f46",  # zinc-700 (base)
        "#52525b",  # zinc-600
        "#71717a",  # zinc-500
        "#a1a1aa",  # zinc-400
        "#d4d4d8",  # zinc-300
        "#f4f4f5",  # zinc-100
        "#ffffff",  # white (peak)
        "#f4f4f5",
        "#d4d4d8",
        "#a1a1aa",
        "#71717a",
        "#52525b",
        "#3f3f46",
    ]

    def _shimmer_text(self, content: str, max_len: int = 120) -> Text:
        """Create text with a sweeping shimmer gradient animation."""
        if len(content) > max_len:
            content = content[: max_len - 1] + "…"
        text = Text()
        if not content:
            return text

        colors = self._SHIMMER_COLORS
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
        streaming = self.tracer.get_streaming_content(agent_id)
        if streaming and streaming.strip():
            # Take the last non-empty line(s)
            lines = [ln for ln in streaming.strip().splitlines() if ln.strip()]
            if lines:
                return lines[-1].strip()

        # Fall back to the most recent chat message
        agent_msgs = [
            m for m in reversed(list(self.tracer.chat_messages))
            if m.get("agent_id") == agent_id and m.get("role") == "assistant"
        ]
        if agent_msgs:
            content = agent_msgs[0].get("content", "")
            lines = [ln for ln in content.strip().splitlines() if ln.strip()]
            if lines:
                return lines[-1].strip()

        # Fall back to last tool being used
        agent_tools = [
            t for t in list(self.tracer.tool_executions.values())
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

    def _build_subagent_dashboard(self, root_agent_id: str) -> Any:
        """Build a renderable dashboard showing snippets of all child agents."""
        children = self._get_child_agents(root_agent_id)
        if not children:
            return None

        renderables: list[Any] = []

        # Section header
        header = Text()
        header.append("─" * 3, style="dim #22d3ee")
        header.append("  Subagent Activity  ", style="bold #22d3ee")
        header.append("─" * 40, style="dim #22d3ee")
        renderables.append(header)

        spinner_frames = _ACTIVITY_SPINNER
        spinner = spinner_frames[self._stats_spinner_frame % len(spinner_frames)]

        status_styles: dict[str, tuple[str, str]] = {
            "running": (spinner, "#22d3ee"),
            "waiting": ("⏸", "#fbbf24"),
            "completed": ("✓", "#22c55e"),
            "failed": ("✗", "#ef4444"),
            "stopped": ("■", "#a1a1aa"),
            "stopping": ("○", "#a1a1aa"),
            "llm_failed": ("✗", "#ef4444"),
        }

        for child in children:
            agent_id = child["id"]
            agent_name = child.get("name", "Agent")
            status = child.get("status", "running")
            icon, color = status_styles.get(status, ("○", "#a1a1aa"))
            is_active = status in ("running", "waiting")

            card = Text()
            # Agent name line with status icon
            card.append(f"  {icon} ", style=color)
            card.append(agent_name, style=f"bold {color}")

            vuln_count = self._agent_vulnerability_count(agent_id)
            if vuln_count > 0:
                card.append(f"  ⚡{vuln_count}", style="bold #ef4444")

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
        tool_data = {
            "tool_name": tool_name,
            "args": args,
            "status": "completed" if is_complete else "running",
            "result": None,
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
        text = Text()

        if is_complete:
            text.append("✓ ", style="green")
        else:
            text.append("● ", style="yellow")

        text.append("Using tool ", style="dim")
        text.append(tool_name, style="bold blue")

        if args:
            for key, value in list(args.items())[:3]:
                text.append("\n  ")
                text.append(key, style="dim")
                text.append(": ")
                display_value = value if len(value) <= 100 else value[:97] + "..."
                text.append(display_value, style="italic" if not is_complete else None)

        return text

    def _get_status_display_content(
        self, agent_id: str, agent_data: dict[str, Any]
    ) -> tuple[Text | None, Text, bool]:
        status = agent_data.get("status", "running")

        def keymap_styled(keys: list[tuple[str, str]]) -> Text:
            t = Text()
            for i, (key, action) in enumerate(keys):
                if i > 0:
                    t.append(" · ", style="dim")
                t.append(key, style="white")
                t.append(" ", style="dim")
                t.append(action, style="dim")
            return t

        simple_statuses: dict[str, tuple[str, str]] = {
            "stopping": ("Agent stopping...", ""),
            "stopped": ("Agent stopped", ""),
            "completed": ("Agent completed", ""),
        }

        if status in simple_statuses:
            msg, _ = simple_statuses[status]
            text = Text()
            text.append(msg)
            return (text, Text(), False)

        if status == "llm_failed":
            error_msg = agent_data.get("error_message", "")
            text = Text()
            if error_msg:
                text.append(error_msg, style="red")
            else:
                text.append("LLM request failed", style="red")
            self._stop_dot_animation()
            keymap = Text()
            keymap.append("Send message to retry", style="dim")
            return (text, keymap, False)

        if status == "waiting":
            keymap = Text()
            keymap.append("Send message to resume", style="dim")
            return (Text(" "), keymap, False)

        if status == "running":
            # Check if this agent is compacting memory
            if agent_id in self.tracer.compacting_agents:
                animated_text = Text()
                animated_text.append_text(self._build_running_ghost_indicator())
                animated_text.append_text(
                    self._get_sweep_animation(self._compact_sweep_colors)
                )
                animated_text.append("Compacting", style="#fbbf24")
                animated_text.append(" memory", style="#d97706")
                return (animated_text, keymap_styled([("ctrl-q", "quit")]), True)
            if self._agent_has_real_activity(agent_id):
                animated_text = Text()
                animated_text.append_text(self._build_running_ghost_indicator())
                animated_text.append_text(self._get_sweep_animation(self._sweep_colors))
                animated_text.append("esc", style="white")
                animated_text.append(" ", style="dim")
                animated_text.append("stop", style="dim")
                return (animated_text, keymap_styled([("ctrl-q", "quit")]), True)
            animated_text = Text()
            animated_text.append_text(self._build_running_ghost_indicator())
            animated_text.append_text(self._get_animated_verb_text(agent_id, "Initializing"))
            return (animated_text, keymap_styled([("ctrl-q", "quit")]), True)

        return (None, Text(), False)

    def _build_running_ghost_indicator(self) -> Text:
        """Render a tiny ghost pulse for running-status affordance."""
        frame_index = self._spinner_frame_index % len(self.MINI_GHOST_EYE_FRAMES)
        eyes = self.MINI_GHOST_EYE_FRAMES[frame_index]
        tail = self.MINI_GHOST_TAIL_FRAMES[frame_index % len(self.MINI_GHOST_TAIL_FRAMES)]
        text = Text()
        text.append(".-.", style="#67e8f9")
        text.append("(", style="#38bdf8")
        text.append(eyes, style="#e2e8f0")
        text.append(")", style="#38bdf8")
        text.append(tail, style="#22d3ee")
        text.append(" ", style="dim")
        return text

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

        vulnerabilities = self.tracer.vulnerability_reports

        if not vulnerabilities:
            vuln_panel.update_vulnerabilities([])
            return

        enriched_vulns = []
        for vuln in vulnerabilities:
            enriched = dict(vuln)
            report_id = vuln.get("id", "")
            agent_name = self._get_agent_name_for_vulnerability(report_id)
            if agent_name:
                enriched["agent_name"] = agent_name
            enriched_vulns.append(enriched)

        vuln_panel.update_vulnerabilities(enriched_vulns)

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

        dot_color = "#0a3d1f"

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
        text = Text()
        sweep = self._get_sweep_animation(self._sweep_colors)
        text.append_text(sweep)
        parts = verb.split(" ", 1)
        text.append(parts[0], style="white")
        if len(parts) > 1:
            text.append(" ", style="dim")
            text.append(parts[1], style="dim")
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

        status_indicators = {
            "running": "◐",
            "waiting": "⏸",
            "completed": "✓",
            "failed": "✗",
            "stopped": "■",
            "stopping": "○",
            "llm_failed": "✗",
        }

        status_icon = status_indicators.get(status, "○")
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

        status_indicators = {
            "running": "◐",
            "waiting": "⏸",
            "completed": "✓",
            "failed": "✗",
            "stopped": "■",
            "stopping": "○",
            "llm_failed": "✗",
        }

        status_icon = status_indicators.get(status, "○")
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
            streaming_result = self._render_streaming_content(content)
            interrupted_text = Text()
            interrupted_text.append("\n")
            interrupted_text.append("⚠ ", style="yellow")
            interrupted_text.append("Interrupted by user", style="yellow dim")
            return Group(streaming_result, interrupted_text)

        return AgentMessageRenderer.render_simple(content)

    def _render_tool_content_simple(self, tool_data: dict[str, Any]) -> Any:
        tool_name = tool_data.get("tool_name", "Unknown Tool")
        args = tool_data.get("args", {})
        status = tool_data.get("status", "unknown")
        result = tool_data.get("result")

        renderer = get_tool_renderer(tool_name)

        if renderer:
            widget = renderer.render(tool_data)
            return widget.renderable

        text = Text()

        if tool_name in ("llm_error_details", "sandbox_error_details"):
            return self._render_error_details(text, tool_name, args)

        text.append("→ Using tool ")
        text.append(tool_name, style="bold blue")

        status_styles = {
            "running": ("●", "yellow"),
            "completed": ("✓", "green"),
            "failed": ("✗", "red"),
            "error": ("✗", "red"),
        }
        icon, style = status_styles.get(status, ("○", "dim"))
        text.append(" ")
        text.append(icon, style=style)

        if args:
            for k, v in list(args.items())[:5]:
                str_v = str(v)
                if len(str_v) > 500:
                    str_v = str_v[:497] + "..."
                text.append("\n  ")
                text.append(k, style="dim")
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
        if tool_name == "llm_error_details":
            text.append("✗ LLM Request Failed", style="red")
        else:
            text.append("✗ Sandbox Initialization Failed", style="red")
            if args.get("error"):
                text.append(f"\n{args['error']}", style="bold red")
        if args.get("details"):
            details = str(args["details"])
            if len(details) > 1000:
                details = details[:997] + "..."
            text.append("\nDetails: ", style="dim")
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

        if self.tracer:
            streaming_content = self.tracer.get_streaming_content(self.selected_agent_id)
            if streaming_content and streaming_content.strip():
                self.tracer.clear_streaming_content(self.selected_agent_id)
                self.tracer.interrupted_content[self.selected_agent_id] = streaming_content
                self.tracer.log_chat_message(
                    content=streaming_content,
                    role="assistant",
                    agent_id=self.selected_agent_id,
                    metadata={"interrupted": True},
                )

        try:
            from esprit.tools.agents_graph.agents_graph_actions import _agent_instances

            if self.selected_agent_id in _agent_instances:
                agent_instance = _agent_instances[self.selected_agent_id]
                if hasattr(agent_instance, "cancel_current_execution"):
                    agent_instance.cancel_current_execution()
        except (ImportError, AttributeError, KeyError):
            pass

        if self.tracer:
            self.tracer.log_chat_message(
                content=message,
                role="user",
                agent_id=self.selected_agent_id,
            )

        try:
            from esprit.tools.agents_graph.agents_graph_actions import send_user_message_to_agent

            send_user_message_to_agent(self.selected_agent_id, message)

        except (ImportError, AttributeError) as e:
            logging.warning(f"Failed to send message to agent {self.selected_agent_id}: {e}")

        self._displayed_events.clear()
        self._update_chat_view()

        self.call_after_refresh(self._focus_chat_input)

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

        try:
            from esprit.tools.agents_graph.agents_graph_actions import stop_agent

            result = stop_agent(agent_id)

            if result.get("success"):
                logging.info(f"Stop request sent to agent: {result.get('message', 'Unknown')}")
            else:
                logging.warning(f"Failed to stop agent: {result.get('error', 'Unknown error')}")

        except Exception:
            logging.exception(f"Failed to stop agent {agent_id}")

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


async def run_tui(args: argparse.Namespace, gui_server: Any = None) -> None:
    """Run esprit in interactive TUI mode with textual."""
    app = EspritTUIApp(args, gui_server=gui_server)
    await app.run_async()
