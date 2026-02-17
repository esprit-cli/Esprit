import os
import webbrowser
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any, ClassVar

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.suggester import Suggester
from textual.widgets import Input, Static

from esprit.config import Config
from esprit.llm.config import DEFAULT_MODEL
from esprit.providers import PROVIDER_NAMES, get_provider_auth
from esprit.providers.base import AuthMethod, OAuthCredentials
from esprit.providers.config import AVAILABLE_MODELS
from esprit.providers.token_store import TokenStore
from esprit.providers.account_pool import get_account_pool

# Providers that use the multi-account pool
from esprit.providers.constants import MULTI_ACCOUNT_PROVIDERS as _MULTI_ACCOUNT_PROVIDERS

# Files that indicate a project root (ordered by priority)
_PROJECT_MARKERS: list[tuple[str, str]] = [
    ("package.json", "Node.js"),
    ("pyproject.toml", "Python"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("pom.xml", "Java/Maven"),
    ("build.gradle", "Java/Gradle"),
    ("Gemfile", "Ruby"),
    ("composer.json", "PHP"),
    ("*.sln", "C#/.NET"),
    ("CMakeLists.txt", "C/C++"),
    ("Makefile", "Make"),
    (".git", "Git"),
]


def get_package_version() -> str:
    try:
        return pkg_version("esprit-cli")
    except PackageNotFoundError:
        return "dev"


def _detect_project(directory: str) -> tuple[str, str | None]:
    """Return (short_name, project_type) for a directory.

    short_name is the last component of the path (e.g. "my-app").
    project_type is a human label like "Node.js" or None if no marker found.
    """
    p = Path(directory).resolve()
    short_name = p.name or str(p)
    for marker, label in _PROJECT_MARKERS:
        if marker.startswith("*"):
            if list(p.glob(marker)):
                return short_name, label
        elif (p / marker).exists():
            return short_name, label
    return short_name, None


class DirectorySuggester(Suggester):
    """Suggests directory paths as the user types."""

    def __init__(self) -> None:
        super().__init__(use_cache=False, case_sensitive=True)

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        try:
            p = Path(value).expanduser()
            # If value ends with / try listing that directory
            if value.endswith("/") or value.endswith(os.sep):
                if p.is_dir():
                    children = sorted(
                        [c for c in p.iterdir() if c.is_dir() and not c.name.startswith(".")],
                        key=lambda x: x.name,
                    )
                    if children:
                        return str(children[0]) + "/"
                return None
            # Otherwise, complete the last component
            parent = p.parent
            prefix = p.name
            if parent.is_dir():
                matches = sorted(
                    [
                        c
                        for c in parent.iterdir()
                        if c.is_dir() and c.name.startswith(prefix) and not c.name.startswith(".")
                    ],
                    key=lambda x: x.name,
                )
                if matches:
                    return str(matches[0]) + "/"
        except OSError:
            pass
        return None


@dataclass(slots=True)
class LaunchpadResult:
    action: str
    target: str | None = None
    scan_mode: str = "deep"


@dataclass(slots=True)
class _MenuEntry:
    key: str
    label: str
    hint: str = ""


class LaunchpadApp(App[LaunchpadResult | None]):  # type: ignore[misc]
    CSS_PATH = "assets/launchpad_styles.tcss"

    BINDINGS: ClassVar[list[Binding]] = [  # type: ignore[assignment]
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_entry", "Select", show=False, priority=True),
        Binding("escape", "go_back", "Back", show=False, priority=True),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("ctrl+c", "quit_app", "Quit", show=False, priority=True),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
        *[Binding(str(n), f"quick_select({n})", str(n), show=False) for n in range(1, 10)],
    ]

    # Ghost pixel art: [] = cyan body, .. = dark (eyes/mouth), * = sparkle
    GHOST_FRAMES: ClassVar[list[tuple[str, ...]]] = [
        (
            "            *             *        ",
            "            [][][][][][][]         ",
            "         [][][][][][][][][][]      ",
            "       [][][][][][][][][][][][][]  ",
            "       [][]..[][][][]..[][][][]    ",
            "       [][]..[][][][]..[][][][]    ",
            "       [][][][][][][][][][][][]    ",
            "       [][][][][]..[][][][][]      ",
            "       [][][][][][][][][][]        ",
            "         [][][][][][][][][]        ",
            "       [][]  [][][]  [][][]        ",
            "       []      [][]    []         ",
        ),
        (
            "         *             *           ",
            "            [][][][][][][]         ",
            "         [][][][][][][][][][]      ",
            "       [][][][][][][][][][][][][]  ",
            "       [][]..[][][][]..[][][][]    ",
            "       [][]..[][][][]..[][][][]    ",
            "       [][][][][][][][][][][][]    ",
            "       [][][][][]..[][][][][]      ",
            "       [][][][][][][][][][]        ",
            "         [][][][][][][][][]        ",
            "         [][]  [][][]  [][]        ",
            "           []    []      []       ",
        ),
        (
            "              *             *      ",
            "            [][][][][][][]         ",
            "         [][][][][][][][][][]      ",
            "       [][][][][][][][][][][][][]  ",
            "       [][]..[][][][]..[][][][]    ",
            "       [][]..[][][][]..[][][][]    ",
            "       [][][][][][][][][][][][]    ",
            "       [][][][][]..[][][][][]      ",
            "       [][][][][][][][][][]        ",
            "         [][][][][][][][][]        ",
            "       [][][]  [][]  [][][]        ",
            "       []        [][]  []         ",
        ),
    ]

    MAIN_OPTIONS: ClassVar[list[_MenuEntry]] = [
        _MenuEntry("scan", "Scan", ""),  # hint filled dynamically with CWD info
        _MenuEntry("model", "Model Config", "Choose default model"),
        _MenuEntry("provider", "Provider Config", "Connect providers (incl. free Antigravity)"),
        _MenuEntry("scan_mode", "Scan Mode", "Set quick, standard, or deep"),
        _MenuEntry("exit", "Exit", "Close launchpad"),
    ]

    selected_index: reactive[int] = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self._token_store = TokenStore()
        self._account_pool = get_account_pool()
        self._current_entries: list[_MenuEntry] = []
        self._current_title = ""
        self._current_hint = ""
        self._view = "main"
        self._history: list[str] = []
        self._selected_provider_id: str | None = None
        self._pending_auth: tuple[str, Any, Any] | None = None
        self._input_mode: str | None = None
        self._scan_mode = "deep"
        self._status = ""
        self._animation_step = 0
        self._ghost_timer: Any | None = None
        self._model_filter = ""

        # Detect current project
        self._cwd = os.getcwd()
        self._project_name, self._project_type = _detect_project(self._cwd)
        self._dir_suggester = DirectorySuggester()

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("", id="launchpad_ghost"),
            Static("", id="launchpad_brand"),
            Static("", id="launchpad_title"),
            Static("", id="launchpad_menu"),
            Input(placeholder="", id="launchpad_input"),
            Static("", id="launchpad_status"),
            Static("", id="launchpad_hint"),
            id="launchpad_root",
        )

    def on_mount(self) -> None:
        self.title = "esprit"
        input_widget = self.query_one("#launchpad_input", Input)
        input_widget.display = False
        self._set_view("main", push=False)
        self._ghost_timer = self.set_interval(0.15, self._tick_animation)

    def on_unmount(self) -> None:
        if self._ghost_timer is not None:
            self._ghost_timer.stop()

    def _tick_animation(self) -> None:
        self._animation_step += 1
        self._render_ghost()

    def _render_ghost(self) -> None:
        ghost = self._build_ghost_text(self._animation_step)
        self.query_one("#launchpad_ghost", Static).update(ghost)

    def _build_ghost_text(self, phase: int) -> Text:
        frame = self.GHOST_FRAMES[phase % len(self.GHOST_FRAMES)]
        ghost = Text()
        for line_index, line in enumerate(frame):
            line_text = Text()
            i = 0
            while i < len(line):
                chunk = line[i : i + 2]
                if chunk == "[]":
                    line_text.append("  ", style=Style(bgcolor="#22d3ee"))
                    i += 2
                    continue
                if chunk == "..":
                    line_text.append("  ", style=Style(bgcolor="#0a0a0a"))
                    i += 2
                    continue

                char = line[i]
                if char == "*":
                    sparkle = "#67e8f9" if (phase + line_index + i) % 2 == 0 else "#38bdf8"
                    line_text.append("\u2727", style=Style(color=sparkle, bold=True))
                elif char == " ":
                    line_text.append(" ")
                else:
                    line_text.append(char)
                i += 1

            ghost.append_text(line_text)
            if line_index < len(frame) - 1:
                ghost.append("\n")
        return ghost

    def _build_brand_text(self) -> Text:
        version = get_package_version()
        brand = Text()
        brand.append("esprit", style=Style(color="#22d3ee", bold=True))
        brand.append("  v" + version, style=Style(color="#555555"))
        return brand

    def _set_status(self, message: str) -> None:
        self._status = message
        status_widget = self.query_one("#launchpad_status", Static)
        if message:
            status_widget.update(Text(message, style=Style(color="#b89292")))
        else:
            status_widget.update("")

    def _set_view(self, view: str, push: bool = True) -> None:  # noqa: PLR0915
        if push and self._view != view:
            self._history.append(self._view)

        self._view = view
        self.selected_index = 0

        input_widget = self.query_one("#launchpad_input", Input)
        input_widget.display = False
        input_widget.value = ""
        input_widget.password = False
        input_widget.suggester = None
        self._input_mode = None

        if view == "main":
            entries = list(self.MAIN_OPTIONS)
            # Dynamically set scan hint with project info
            project_hint = self._project_name
            if self._project_type:
                project_hint += f" ({self._project_type})"
            for i, e in enumerate(entries):
                if e.key == "scan":
                    entries[i] = _MenuEntry("scan", "Scan", project_hint)
                    break
            self._current_entries = entries
            self._current_title = ""
            self._current_hint = "up/down to navigate  enter to select  q to quit"
        elif view == "scan_choose":
            self._current_entries = self._build_scan_target_entries()
            self._current_title = "Scan Target"
            self._current_hint = "select target type  esc to go back"
        elif view == "model":
            self._model_filter = ""
            self._current_entries = self._build_model_entries()
            self._current_title = "Model Config"
            self._current_hint = "type to search  up/down to navigate  enter to select  esc to go back"
            self._input_mode = "model_search"
            input_widget.placeholder = "search models..."
            input_widget.display = True
            input_widget.focus()
        elif view == "provider":
            self._current_entries = self._build_provider_entries()
            self._current_title = "Provider Config"
            self._current_hint = "select a provider  esc to go back"
        elif view == "provider_actions":
            self._current_entries = self._build_provider_action_entries()
            provider_name = PROVIDER_NAMES.get(self._selected_provider_id or "", "Provider")
            self._current_title = provider_name
            self._current_hint = "choose an action  esc to go back"
        elif view == "scan_mode":
            self._current_entries = self._build_scan_mode_entries()
            self._current_title = "Scan Mode"
            self._current_hint = "quick = fast  deep = thorough  esc to go back"
        elif view == "scan_target":
            self._current_entries = []
            self._current_title = "Scan Target"
            self._current_hint = "enter URL, repo, or path  esc to go back"
            self._input_mode = "scan_target"
            input_widget.placeholder = "https://example.com, github.com/org/repo, or /path"
            input_widget.display = True
            input_widget.suggester = None
            input_widget.focus()
        elif view == "scan_local":
            self._current_entries = []
            self._current_title = "Local Path"
            self._current_hint = "type a path (tab to accept suggestion)  esc to go back"
            self._input_mode = "scan_local"
            input_widget.placeholder = "/path/to/project"
            input_widget.suggester = self._dir_suggester
            input_widget.display = True
            input_widget.focus()
        elif view == "provider_code":
            self._current_entries = []
            self._current_title = "OAuth Code"
            self._current_hint = "paste code from browser and press enter  esc to go back"
            self._input_mode = "provider_code"
            input_widget.placeholder = "paste authorization code"
            input_widget.display = True
            input_widget.focus()
        elif view == "provider_api_key":
            self._current_entries = []
            self._current_title = "API Key"
            self._current_hint = "enter your API key and press enter  esc to go back"
            self._input_mode = "provider_api_key"
            input_widget.placeholder = "sk-..."
            input_widget.password = True
            input_widget.display = True
            input_widget.focus()

        self._render_panel()

    def _build_model_entries(self, filter_text: str = "") -> list[_MenuEntry]:
        current = Config.get("esprit_llm") or DEFAULT_MODEL
        entries: list[_MenuEntry] = []
        query = filter_text.lower().strip()

        # Provider badges and display info
        _BADGES: dict[str, str] = {
            "antigravity": "AG",
            "esprit": "ES",
            "openai": "OAI",
            "anthropic": "CC",
            "google": "GG",
            "github-copilot": "CO",
        }
        _PROVIDER_LABELS: dict[str, str] = {
            "antigravity": "ANTIGRAVITY",
            "esprit": "ESPRIT",
            "openai": "OPENAI",
            "anthropic": "ANTHROPIC",
            "google": "GOOGLE",
            "github-copilot": "COPILOT",
        }

        # Providers known to be free
        _FREE_PROVIDERS = {"antigravity"}

        # Lazy-load pricing DB for context/cost info
        try:
            from esprit.llm.pricing import get_pricing_db
            pricing_db = get_pricing_db()
        except Exception:  # noqa: BLE001
            pricing_db = None

        # Check which providers are connected
        connected: dict[str, bool] = {}
        for provider_id in AVAILABLE_MODELS:
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                connected[provider_id] = self._account_pool.has_accounts(provider_id)
            else:
                connected[provider_id] = self._token_store.has_credentials(provider_id)

        # Sort: connected providers first
        providers_sorted = sorted(
            AVAILABLE_MODELS.keys(),
            key=lambda p: (0 if connected.get(p) else 1, p),
        )

        for provider_id in providers_sorted:
            models = AVAILABLE_MODELS[provider_id]
            badge = _BADGES.get(provider_id, provider_id[:3].upper())
            label = _PROVIDER_LABELS.get(provider_id, provider_id.upper())
            is_connected = connected.get(provider_id, False)
            is_free_provider = provider_id in _FREE_PROVIDERS

            # Filter models
            matching_models = []
            for model_id, model_name in models:
                full_model = f"{provider_id}/{model_id}"
                searchable = f"{model_name} {model_id} {badge} {label}".lower()
                if is_free_provider:
                    searchable += " free"
                if query and query not in searchable:
                    continue
                matching_models.append((model_id, model_name, full_model))

            if not matching_models:
                continue

            # Provider section header
            conn_indicator = "\u2713" if is_connected else "\u00b7"
            conn_label = "connected" if is_connected else "not connected"
            header_hint = f"[{badge}] {conn_label}"
            if is_free_provider:
                header_hint += "  FREE"
            entries.append(_MenuEntry(
                f"separator:{provider_id}",
                f"  {conn_indicator} {label}",
                header_hint,
            ))

            # Model entries with pricing/context info
            for model_id, model_name, full_model in matching_models:
                is_current = full_model == current
                marker = "\u25cf" if is_current else "\u25cb"
                dim = "" if is_connected else "\u2022 "

                # Build hint with context + pricing info
                hint_parts: list[str] = []

                if pricing_db:
                    try:
                        ctx = pricing_db.get_context_limit(model_id)
                        if ctx and ctx > 0:
                            if ctx >= 1_000_000:
                                hint_parts.append(f"{ctx // 1_000_000}M ctx")
                            else:
                                hint_parts.append(f"{ctx // 1000}K ctx")
                    except Exception:  # noqa: BLE001
                        pass

                    if not is_free_provider:
                        try:
                            pricing = pricing_db.get_pricing(model_id)
                            if pricing and pricing.input_cost > 0:
                                cost_per_m = pricing.input_cost * 1_000_000
                                hint_parts.append(f"${cost_per_m:.1f}/M in")
                        except Exception:  # noqa: BLE001
                            pass

                is_free = is_free_provider or "(free)" in model_name.lower()
                if is_free and not any("free" in p.lower() for p in hint_parts):
                    hint_parts.insert(0, "FREE")

                if is_current:
                    hint_parts.append("\u2605 active")

                hint = "  ".join(hint_parts) if hint_parts else badge

                model_label = f"    {marker} {dim}{model_name}"

                entries.append(_MenuEntry(
                    f"model:{full_model}",
                    model_label,
                    hint,
                ))

        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _build_provider_entries(self) -> list[_MenuEntry]:
        provider_order = ["antigravity", "anthropic", "openai", "google", "github-copilot"]
        entries: list[_MenuEntry] = []

        for provider_id in provider_order:
            provider_name = PROVIDER_NAMES.get(provider_id, provider_id)
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                count = self._account_pool.account_count(provider_id)
                connected = count > 0
                if connected:
                    status = f"{count} account{'s' if count != 1 else ''}"
                else:
                    status = "not connected"
            else:
                connected = self._token_store.has_credentials(provider_id)
                status = "connected" if connected else "not connected"
            marker = "\u25cf" if connected else "\u25cb"
            entries.append(
                _MenuEntry(f"provider:{provider_id}", f"{marker} {provider_name}", hint=status)
            )

        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _build_provider_action_entries(self) -> list[_MenuEntry]:
        provider_id = self._selected_provider_id or ""
        entries = [_MenuEntry("provider_oauth", "Connect via OAuth")]
        if provider_id != "github-copilot":
            entries.append(_MenuEntry("provider_api_key", "Set API Key"))
        entries.append(_MenuEntry("provider_logout", "Logout"))
        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _build_scan_mode_entries(self) -> list[_MenuEntry]:
        entries: list[_MenuEntry] = []
        for mode in ["quick", "standard", "deep"]:
            marker = "\u25cf" if mode == self._scan_mode else "\u25cb"
            entries.append(_MenuEntry(f"scan_mode:{mode}", f"{marker} {mode.title()}"))
        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _build_scan_target_entries(self) -> list[_MenuEntry]:
        entries: list[_MenuEntry] = []

        # Primary: current directory
        label = "This project"
        hint = self._project_name
        if self._project_type:
            hint += f" \u00b7 {self._project_type}"
        entries.append(_MenuEntry("scan_cwd", label, hint=hint))

        # Alternatives
        entries.append(_MenuEntry("scan_target_input", "Enter target", hint="URL, repo, or path"))
        entries.append(_MenuEntry("scan_local_input", "Browse local", hint="with path suggestions"))
        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _render_panel(self) -> None:
        # Brand (only on main view)
        brand_widget = self.query_one("#launchpad_brand", Static)
        if self._view == "main":
            brand_widget.update(self._build_brand_text())
            brand_widget.display = True
        else:
            brand_widget.display = False

        # Ghost (only on main view)
        ghost_widget = self.query_one("#launchpad_ghost", Static)
        if self._view == "main":
            ghost_widget.display = True
            self._render_ghost()
        else:
            ghost_widget.display = False

        # Title
        title_widget = self.query_one("#launchpad_title", Static)
        if self._current_title:
            title_widget.update(
                Text(self._current_title, style=Style(color="#22d3ee", bold=True))
            )
            title_widget.display = True
        else:
            title_widget.display = False

        # Hint
        self.query_one("#launchpad_hint", Static).update(
            Text(self._current_hint, style=Style(color="#555555", italic=True))
        )

        # Menu
        self._render_menu()

    def _render_menu(self) -> None:
        menu_widget = self.query_one("#launchpad_menu", Static)
        if not self._current_entries:
            menu_widget.update("")
            return

        # Show number shortcuts on views with few selectable entries
        show_numbers = self._view in ("main", "scan_choose", "provider", "provider_actions", "scan_mode")
        selectable_num = 0

        menu_text = Text()
        for idx, entry in enumerate(self._current_entries):
            is_selected = idx == self.selected_index
            is_separator = entry.key.startswith("separator:")

            if not is_separator:
                selectable_num += 1

            if is_separator:
                # Provider group header — not selectable
                menu_text.append(entry.label, style=Style(color="#67e8f9", bold=True))
                if entry.hint:
                    # Highlight "FREE" in the hint
                    hint = entry.hint
                    if "FREE" in hint:
                        parts = hint.split("FREE")
                        menu_text.append(f"  {parts[0]}", style=Style(color="#555555"))
                        menu_text.append("FREE", style=Style(color="#22c55e", bold=True))
                        if len(parts) > 1:
                            menu_text.append(parts[1], style=Style(color="#555555"))
                    else:
                        menu_text.append(f"  {entry.hint}", style=Style(color="#555555"))
            elif is_selected:
                prefix = "\u276f "
                label_style = Style(color="#22d3ee", bold=True)
                menu_text.append(prefix, style=label_style)
                menu_text.append(entry.label, style=label_style)
                if entry.hint:
                    self._append_styled_hint(menu_text, entry.hint, selected=True)
                if show_numbers and selectable_num <= 9:
                    menu_text.append(f"  {selectable_num}", style=Style(color="#22d3ee", dim=True))
            else:
                prefix = "  "
                label_style = Style(color="#8a8a8a")
                menu_text.append(prefix, style=label_style)
                menu_text.append(entry.label, style=label_style)
                if entry.hint:
                    self._append_styled_hint(menu_text, entry.hint, selected=False)
                if show_numbers and selectable_num <= 9:
                    menu_text.append(f"  {selectable_num}", style=Style(color="#3f3f3f"))

            if idx < len(self._current_entries) - 1:
                menu_text.append("\n")

        menu_widget.update(menu_text)

    def _append_styled_hint(self, text: Text, hint: str, selected: bool) -> None:
        """Append a hint with special styling for keywords like FREE, active, ctx."""
        text.append("  ", style=Style(color="#555555"))
        parts = hint.split("  ")
        for i, part in enumerate(parts):
            if i > 0:
                text.append("  ", style=Style(color="#3f3f3f"))
            part_stripped = part.strip()
            if part_stripped == "FREE":
                text.append("FREE", style=Style(color="#22c55e", bold=True))
            elif "\u2605 active" in part_stripped:
                text.append(part_stripped, style=Style(color="#fbbf24", bold=True))
            elif "ctx" in part_stripped:
                text.append(part_stripped, style=Style(color="#0e7490" if selected else "#555555"))
            elif part_stripped.startswith("$"):
                text.append(part_stripped, style=Style(color="#555555", dim=True))
            else:
                text.append(part_stripped, style=Style(color="#0e7490" if selected else "#555555"))

    # ── Actions (bound to keys via BINDINGS) ──────────────────────────

    def action_cursor_up(self) -> None:
        if self._input_mode and self._input_mode != "model_search":
            return
        if self._current_entries:
            new_idx = (self.selected_index - 1) % len(self._current_entries)
            # Skip separator entries
            attempts = len(self._current_entries)
            while self._current_entries[new_idx].key.startswith("separator:") and attempts > 0:
                new_idx = (new_idx - 1) % len(self._current_entries)
                attempts -= 1
            self.selected_index = new_idx
            self._render_menu()

    def action_cursor_down(self) -> None:
        if self._input_mode and self._input_mode != "model_search":
            return
        if self._current_entries:
            new_idx = (self.selected_index + 1) % len(self._current_entries)
            # Skip separator entries
            attempts = len(self._current_entries)
            while self._current_entries[new_idx].key.startswith("separator:") and attempts > 0:
                new_idx = (new_idx + 1) % len(self._current_entries)
                attempts -= 1
            self.selected_index = new_idx
            self._render_menu()

    async def action_quick_select(self, number: int) -> None:
        """Jump to and select the Nth selectable entry (1-based)."""
        if self._input_mode:
            return
        # Build map of selectable entries (skip separators)
        selectable = [
            (idx, entry)
            for idx, entry in enumerate(self._current_entries)
            if not entry.key.startswith("separator:")
        ]
        if 1 <= number <= len(selectable):
            target_idx, target_entry = selectable[number - 1]
            self.selected_index = target_idx
            self._render_menu()
            await self._activate_entry(target_entry)

    async def action_select_entry(self) -> None:
        if self._input_mode == "model_search":
            # In model search: enter selects the highlighted model, not the input
            if self._current_entries and not self._current_entries[self.selected_index].key.startswith("separator:"):
                await self._activate_entry(self._current_entries[self.selected_index])
            return
        if self._input_mode:
            # Priority binding intercepted enter; forward it to the Input widget
            input_widget = self.query_one("#launchpad_input", Input)
            await input_widget.action_submit()
            return
        if self._current_entries:
            await self._activate_entry(self._current_entries[self.selected_index])

    def action_go_back(self) -> None:
        if self._input_mode:
            self._set_status("")
            self._go_back()
            return
        if self._view == "main":
            self.exit(LaunchpadResult(action="exit", scan_mode=self._scan_mode))
        else:
            self._go_back()

    def action_quit_app(self) -> None:
        self.exit(LaunchpadResult(action="exit", scan_mode=self._scan_mode))

    # ── Entry activation ──────────────────────────────────────────────

    async def _activate_entry(self, entry: _MenuEntry) -> None:  # noqa: PLR0911, PLR0912
        key = entry.key

        if key == "model":
            self._set_view("model")
            return
        if key == "provider":
            self._set_view("provider")
            return
        if key == "scan_mode":
            self._set_view("scan_mode")
            return
        if key == "scan":
            self._set_view("scan_choose")
            return
        if key == "exit":
            self.exit(LaunchpadResult(action="exit", scan_mode=self._scan_mode))
            return
        if key == "back":
            self._go_back()
            return

        if key.startswith("provider:"):
            self._selected_provider_id = key.split(":", 1)[1]
            self._set_view("provider_actions")
            return

        if key.startswith("model:"):
            model_name = key.split(":", 1)[1]
            os.environ["ESPRIT_LLM"] = model_name
            Config.save_current()
            self._set_status(f"Model set: {model_name}")
            self._set_view("model", push=False)
            return

        if key.startswith("scan_mode:"):
            mode = key.split(":", 1)[1]
            self._scan_mode = mode
            self._set_status(f"Scan mode: {mode}")
            self._set_view("scan_mode", push=False)
            return

        if key == "scan_cwd":
            self.exit(LaunchpadResult(action="scan", target=self._cwd, scan_mode=self._scan_mode))
            return
        if key == "scan_target_input":
            self._set_view("scan_target")
            return
        if key == "scan_local_input":
            self._set_view("scan_local")
            return

        if key == "provider_oauth":
            await self._connect_selected_provider()
            return

        if key == "provider_api_key":
            self._set_view("provider_api_key")
            return

        if key == "provider_logout":
            provider_id = self._selected_provider_id
            if not provider_id:
                return
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                accounts = self._account_pool.list_accounts(provider_id)
                for acct in accounts:
                    self._account_pool.remove_account(provider_id, acct.email)
                if accounts:
                    self._set_status(f"Removed {len(accounts)} account(s) from {PROVIDER_NAMES.get(provider_id, provider_id)}")
                else:
                    self._set_status("No credentials to remove")
            elif self._token_store.delete(provider_id):
                self._set_status(f"Logged out from {PROVIDER_NAMES.get(provider_id, provider_id)}")
            else:
                self._set_status("No credentials to remove")
            self._set_view("provider", push=False)

    def _go_back(self) -> None:
        if not self._history:
            self._set_view("main", push=False)
            return
        previous = self._history.pop()
        self._set_view(previous, push=False)

    # ── OAuth flow ────────────────────────────────────────────────────

    async def _connect_selected_provider(self) -> None:
        provider_id = self._selected_provider_id
        if not provider_id:
            return

        provider = get_provider_auth(provider_id)
        if not provider:
            self._set_status("Provider not available")
            return

        provider_name = PROVIDER_NAMES.get(provider_id, provider_id)
        self._set_status(f"Starting OAuth for {provider_name}...")

        provider_impl: Any = provider

        try:
            auth_result = await provider_impl.authorize()
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"OAuth failed: {exc}")
            return

        opened = webbrowser.open(auth_result.url)
        if not opened:
            self._set_status(f"Open manually: {auth_result.url}")
        else:
            self._set_status(f"Browser opened for {provider_name}")

        if auth_result.method == AuthMethod.CODE:
            self._pending_auth = (provider_id, provider, auth_result)
            self._set_view("provider_code")
            return

        self._set_status(f"Waiting for {provider_name}...")
        callback_result = await provider_impl.callback(auth_result)
        await self._handle_provider_callback(provider_id, callback_result)

    async def _handle_provider_callback(self, provider_id: str, callback_result: Any) -> None:
        if not callback_result.success:
            self._set_status(f"Login failed: {callback_result.error}")
            self._set_view("provider", push=False)
            return

        if callback_result.credentials:
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                email = callback_result.credentials.extra.get("email", "unknown") if callback_result.credentials.extra else "unknown"
                if not email or email == "unknown":
                    email = callback_result.credentials.account_id or f"account-{self._account_pool.account_count(provider_id) + 1}"
                # Ensure email stored in extra for token refresh lookup
                if callback_result.credentials.extra is None:
                    callback_result.credentials.extra = {}
                callback_result.credentials.extra["email"] = email
                self._account_pool.add_account(provider_id, callback_result.credentials, email)
            else:
                self._token_store.set(provider_id, callback_result.credentials)
        self._set_status(f"Connected {PROVIDER_NAMES.get(provider_id, provider_id)}")
        self._set_view("provider", push=False)

    # ── Input change (live search) ─────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._input_mode == "model_search":
            self._model_filter = event.value
            self._current_entries = self._build_model_entries(self._model_filter)
            self.selected_index = 0
            # Skip separator to first selectable entry
            while (
                self.selected_index < len(self._current_entries)
                and self._current_entries[self.selected_index].key.startswith("separator:")
            ):
                self.selected_index += 1
            self._render_menu()

    # ── Input submission ──────────────────────────────────────────────

    async def on_input_submitted(self, event: Input.Submitted) -> None:  # noqa: PLR0911
        value = event.value.strip()

        if self._input_mode == "scan_target":
            if not value:
                self._set_status("Target is required")
                return
            self.exit(LaunchpadResult(action="scan", target=value, scan_mode=self._scan_mode))
            return

        if self._input_mode == "scan_local":
            if not value:
                self._set_status("Path is required")
                return
            resolved = str(Path(value).expanduser().resolve())
            if not Path(resolved).exists():
                self._set_status(f"Path not found: {resolved}")
                return
            self.exit(LaunchpadResult(action="scan", target=resolved, scan_mode=self._scan_mode))
            return

        if self._input_mode == "provider_api_key":
            provider_id = self._selected_provider_id
            if not provider_id:
                self._set_status("No provider selected")
                self._go_back()
                return
            if not value:
                self._set_status("API key cannot be empty")
                return

            creds = OAuthCredentials(type="api", access_token=value)
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                self._account_pool.add_account(provider_id, creds, f"api-key-{self._account_pool.account_count(provider_id) + 1}")
            else:
                self._token_store.set(provider_id, creds)
            self._set_status(f"Saved API key for {PROVIDER_NAMES.get(provider_id, provider_id)}")
            self._set_view("provider", push=False)
            return

        if self._input_mode == "provider_code":
            pending = self._pending_auth
            if not pending:
                self._set_status("No pending authorization")
                self._set_view("provider", push=False)
                return
            if not value:
                self._set_status("Code cannot be empty")
                return

            provider_id, provider, auth_result = pending
            provider_impl: Any = provider
            self._set_status("Completing OAuth...")
            callback_result = await provider_impl.callback(auth_result, value)
            self._pending_auth = None
            await self._handle_provider_callback(provider_id, callback_result)


async def run_launchpad() -> LaunchpadResult | None:
    app = LaunchpadApp()
    return await app.run_async()
