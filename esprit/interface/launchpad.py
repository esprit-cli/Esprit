import asyncio
import os
import random
import time
import webbrowser
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any, ClassVar

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.suggester import Suggester
from textual.widgets import Input, Static

from esprit.config import Config
from esprit.interface.utils import infer_target_type
from esprit.llm.config import DEFAULT_MODEL
from esprit.providers import PROVIDER_NAMES, get_provider_auth
from esprit.providers.base import AuthCallbackResult, AuthMethod, OAuthCredentials
from esprit.providers.config import get_available_models, get_public_opencode_models
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


def _friendly_login_error(exc: Exception) -> str:
    """Classify common login/OAuth errors into user-friendly messages."""
    msg = str(exc).lower()
    if "timeout" in msg or "timed out" in msg:
        return "Authorization timed out. Please try logging in again."
    if "connection" in msg or "network" in msg or "dns" in msg:
        return "Connection failed. Check your internet connection and try again."
    if "401" in msg or "unauthorized" in msg or "invalid" in msg:
        return "Invalid credentials. Please check your API key and try again."
    if "expired" in msg:
        return "Session expired. Please log in again."
    return f"Login failed: {exc}. Please try again or use a different provider."


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

    def __init__(self, base_dir: str | None = None) -> None:
        super().__init__(use_cache=False, case_sensitive=True)
        self._base_dir = Path(base_dir or os.getcwd()).expanduser().resolve()

    def set_base_dir(self, base_dir: str) -> None:
        try:
            self._base_dir = Path(base_dir).expanduser().resolve()
        except OSError:
            self._base_dir = Path(os.getcwd()).expanduser().resolve()

    def _resolve_candidate_path(self, value: str) -> Path:
        candidate = Path(value).expanduser()
        if candidate.is_absolute():
            return candidate
        return (self._base_dir / candidate)

    def _format_suggestion(self, original: str, suggestion: Path) -> str:
        if original.startswith("~"):
            home = Path.home()
            try:
                relative = suggestion.relative_to(home)
                if relative == Path("."):
                    return "~/"
                return f"~/{relative.as_posix()}/"
            except ValueError:
                return str(suggestion) + "/"

        if original.startswith("/") or original.startswith(os.sep):
            return str(suggestion) + "/"

        if "/" in original:
            prefix = original if original.endswith("/") else original.rsplit("/", 1)[0] + "/"
            return f"{prefix}{suggestion.name}/"

        if os.sep in original and os.sep != "/":
            prefix = original if original.endswith(os.sep) else original.rsplit(os.sep, 1)[0] + os.sep
            return f"{prefix}{suggestion.name}{os.sep}"

        try:
            relative = suggestion.relative_to(self._base_dir)
            return f"{relative.as_posix()}/"
        except ValueError:
            return suggestion.name + "/"

    async def get_suggestion(self, value: str) -> str | None:
        if not value:
            return None
        try:
            raw = value.strip()
            candidate = self._resolve_candidate_path(raw)

            if raw == ".":
                parent = self._base_dir
                prefix = "."
            elif raw.endswith("/") or raw.endswith(os.sep):
                parent = candidate
                prefix = ""
            else:
                parent = candidate.parent
                prefix = candidate.name

            if not parent.is_dir():
                return None

            show_hidden = prefix.startswith(".")
            children = sorted(
                [
                    c for c in parent.iterdir()
                    if c.is_dir() and (show_hidden or not c.name.startswith("."))
                ],
                key=lambda x: x.name.lower(),
            )
            if prefix:
                children = [
                    c for c in children
                    if c.name.lower().startswith(prefix.lower())
                ]

            if children:
                return self._format_suggestion(raw, children[0])
        except OSError:
            pass
        return None


@dataclass(slots=True)
class LaunchpadResult:
    action: str
    target: str | None = None
    scan_mode: str = "deep"
    prechecked: bool = False


@dataclass(slots=True)
class _MenuEntry:
    key: str
    label: str
    hint: str = ""


@dataclass(frozen=True, slots=True)
class _LaunchpadTheme:
    key: str
    label: str
    hint: str
    accent: str
    selected_hint: str
    menu_label: str
    menu_hint: str
    separator: str
    info: str
    status: str
    brand_dim: str
    ghost_body: str
    ghost_face: str
    sparkle_a: str
    sparkle_b: str


class LaunchpadApp(App[LaunchpadResult | None]):  # type: ignore[misc]
    CSS_PATH = "assets/launchpad_styles.tcss"
    DEFAULT_THEME = "esprit"

    BINDINGS: ClassVar[list[Binding]] = [  # type: ignore[assignment]
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_entry", "Select", show=False, priority=True),
        Binding("escape", "go_back", "Back", show=False, priority=True),
        Binding("slash", "start_search", "Search", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("ctrl+c", "quit_app", "Quit", show=False, priority=True),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
    ]

    # Ghost pixel art: the Esprit flying ghost mascot
    GHOST: ClassVar[tuple[str, ...]] = (
        "         ▄▄█████████▄▄",
        "        ██▀         ▀▀█▄",
        "      ▄▀▄██   ██▄      ▀█▄",
        "      █ ███   █████▄     ▀█",
        "      █ ▀██   █████▀      ▀█",
        " ▄▄▄▄▄█    ▄▄▄▄   ▄▄▄▄▄    ▀█",
        "██▄██ ▀█▄   ▀█▀ ▄██   ▀██   ▀█▄",
        "██████▄ ▀▀    ▄▄▀             █▄",
        "  ▀▀████▄▄█▄  ██▄▄▄█▄▄        ▀█",
        "     ▀██████    ▀██████▄       ▀█▄",
        "        ▀▀███▄   ▀▀▀█▀██         ▀▄",
        "           ▀███▄                  ██▄",
        "             ▀▀██▄▄   ▄▄▄          ▀▀█▄▄",
        "                ▀▀███████▄ ███         ▀▀█",
        "                   ▀▀▀███████████▄▄▀███▄█▀",
        "                       ▀▀▀▀▀█████▀▀▀▀",
    )
    GLITCH_CHARS: ClassVar[str] = "█▓▒░╔╗╚╝║═╬╣╠╩╦@#$%&*"
    _GLITCH_RESOLVE_STEPS: ClassVar[int] = 12

    MAIN_OPTIONS: ClassVar[list[_MenuEntry]] = [
        _MenuEntry("scan", "Scan", ""),  # hint filled dynamically with CWD info
        _MenuEntry("model", "Model Config", "Choose default model"),
        _MenuEntry("provider", "Provider Config", "Connect providers (incl. free Antigravity)"),
        _MenuEntry("scan_mode", "Scan Mode", "Set quick, standard, or deep"),
        _MenuEntry("theme", "Theme", "Select launchpad theme"),
        _MenuEntry("exit", "Exit", "Close launchpad"),
    ]
    THEMES: ClassVar[dict[str, _LaunchpadTheme]] = {
        "esprit": _LaunchpadTheme(
            key="esprit",
            label="Esprit",
            hint="Neon cyan + noir",
            accent="#22d3ee",
            selected_hint="#0e7490",
            menu_label="#8a8a8a",
            menu_hint="#555555",
            separator="#67e8f9",
            info="#8a8a8a",
            status="#b89292",
            brand_dim="#555555",
            ghost_body="#22d3ee",
            ghost_face="#0a0a0a",
            sparkle_a="#67e8f9",
            sparkle_b="#38bdf8",
        ),
        "ember": _LaunchpadTheme(
            key="ember",
            label="Ember",
            hint="Molten amber + charcoal",
            accent="#f97316",
            selected_hint="#ea580c",
            menu_label="#c6b8a5",
            menu_hint="#6f5e4f",
            separator="#fdba74",
            info="#c6b8a5",
            status="#d6a07b",
            brand_dim="#7d6857",
            ghost_body="#fb923c",
            ghost_face="#1c140f",
            sparkle_a="#fdba74",
            sparkle_b="#f97316",
        ),
        "matrix": _LaunchpadTheme(
            key="matrix",
            label="Matrix",
            hint="Signal green + black",
            accent="#22c55e",
            selected_hint="#15803d",
            menu_label="#9fbfa7",
            menu_hint="#4f6b57",
            separator="#86efac",
            info="#9fbfa7",
            status="#7fb48b",
            brand_dim="#4f6b57",
            ghost_body="#22c55e",
            ghost_face="#05140b",
            sparkle_a="#86efac",
            sparkle_b="#22c55e",
        ),
        "glacier": _LaunchpadTheme(
            key="glacier",
            label="Glacier",
            hint="Ice blue + deep navy",
            accent="#38bdf8",
            selected_hint="#0c4a6e",
            menu_label="#9cb4c8",
            menu_hint="#5a6f82",
            separator="#7dd3fc",
            info="#9cb4c8",
            status="#9ab9d3",
            brand_dim="#5a6f82",
            ghost_body="#38bdf8",
            ghost_face="#04131c",
            sparkle_a="#7dd3fc",
            sparkle_b="#38bdf8",
        ),
        "crt": _LaunchpadTheme(
            key="crt",
            label="CRT",
            hint="Phosphor green + scanlines",
            accent="#33ff33",
            selected_hint="#1fcc1f",
            menu_label="#9bcf9b",
            menu_hint="#4f7a4f",
            separator="#66ff66",
            info="#9bcf9b",
            status="#7fb47f",
            brand_dim="#4f7a4f",
            ghost_body="#33ff33",
            ghost_face="#001400",
            sparkle_a="#99ff99",
            sparkle_b="#33ff33",
        ),
        "sakura": _LaunchpadTheme(
            key="sakura",
            label="Sakura",
            hint="Cherry pink + plum",
            accent="#f472b6",
            selected_hint="#be185d",
            menu_label="#f1c6dd",
            menu_hint="#8f5f78",
            separator="#f9a8d4",
            info="#f1c6dd",
            status="#d5a1bf",
            brand_dim="#8f5f78",
            ghost_body="#f472b6",
            ghost_face="#2a0f1f",
            sparkle_a="#f9a8d4",
            sparkle_b="#ec4899",
        ),
    }

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
        self._status_timer: Any | None = None
        self._animation_step = 0
        self._ghost_timer: Any | None = None
        self._oauth_timer: Any | None = None
        self._oauth_task: asyncio.Task[Any] | None = None
        self._oauth_start_time: float = 0.0
        self._model_filter = ""
        self._unfiltered_entries: list[_MenuEntry] = []
        self._menu_top_row: int = 0
        self._pending_scan_target: str | None = None
        self._theme_id = self._normalize_theme_id(Config.get_launchpad_theme())
        self._runtime_profile = self._normalize_runtime_profile(Config.get_runtime_profile())

        # First-run wizard state
        self._wizard_mode = False

        # Detect current project
        self._cwd = os.getcwd()
        self._project_name, self._project_type = _detect_project(self._cwd)
        self._dir_suggester = DirectorySuggester(self._cwd)

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
        self._apply_theme_class()
        input_widget = self.query_one("#launchpad_input", Input)
        input_widget.display = False
        if self._is_first_run():
            self._wizard_mode = True
            self._set_view("wizard_welcome", push=False)
        else:
            self._set_view("main", push=False)
        self._ghost_timer = self.set_interval(0.10, self._tick_animation)

    def on_unmount(self) -> None:
        if self._ghost_timer is not None:
            self._ghost_timer.stop()
        self._stop_oauth_timer()
        self._cancel_oauth_task()

    def _tick_animation(self) -> None:
        self._animation_step += 1
        self._render_ghost()

    def _normalize_theme_id(self, theme_id: str | None) -> str:
        if theme_id and theme_id in self.THEMES:
            return theme_id
        return self.DEFAULT_THEME

    @staticmethod
    def _normalize_runtime_profile(profile: str | None) -> str:
        normalized = (profile or "").strip().lower()
        if normalized in {"cloud", "connectors"}:
            return normalized
        return "cloud"

    def _active_theme(self) -> _LaunchpadTheme:
        return self.THEMES[self._theme_id]

    def _has_active_screen(self) -> bool:
        try:
            _ = self.screen
        except ScreenStackError:
            return False
        return True

    def _apply_theme_class(self) -> None:
        if not self._has_active_screen():
            return
        screen = self.screen
        for theme_id in self.THEMES:
            screen.remove_class(f"theme-{theme_id}")
        screen.add_class(f"theme-{self._theme_id}")

    _WIZARD_MARKER = Path.home() / ".esprit" / ".wizard_done"

    def _is_first_run(self) -> bool:
        """Check if this is the first run (no credentials and wizard not completed)."""
        if self._WIZARD_MARKER.exists():
            return False
        creds_file = Path.home() / ".esprit" / "credentials.json"
        if creds_file.exists():
            return False
        if self._configured_provider_rows():
            return False
        return True

    def _mark_wizard_done(self) -> None:
        """Write a marker file so the wizard is not shown again."""
        try:
            self._WIZARD_MARKER.parent.mkdir(parents=True, exist_ok=True)
            self._WIZARD_MARKER.touch()
        except OSError:
            pass

    def _set_theme(self, theme_id: str, persist: bool = True) -> bool:
        next_theme = self._normalize_theme_id(theme_id)
        changed = next_theme != self._theme_id
        self._theme_id = next_theme

        if self._has_active_screen():
            self._apply_theme_class()
            self._render_panel()

        if persist and changed:
            if Config.save_launchpad_theme(next_theme):
                self._set_status(f"Theme set: {self._active_theme().label}", "success")
            else:
                self._set_status("Failed to save theme", "error")

        return changed

    def _set_runtime_profile(self, profile: str, persist: bool = True) -> bool:
        next_profile = self._normalize_runtime_profile(profile)
        changed = next_profile != self._runtime_profile
        self._runtime_profile = next_profile

        if persist and changed:
            Config.save_runtime_profile(next_profile)

        return changed

    def _render_ghost(self) -> None:
        ghost = self._build_ghost_text(self._animation_step)
        self.query_one("#launchpad_ghost", Static).update(ghost)

    def _build_ghost_text(self, phase: int) -> Text:
        theme = self._active_theme()
        progress = min(1.0, phase / self._GLITCH_RESOLVE_STEPS)
        ghost = Text()
        for line_index, line in enumerate(self.GHOST):
            line_text = Text()
            for char in line:
                if char == " ":
                    line_text.append(char)
                elif random.random() < progress:
                    line_text.append(char, style=Style(color=theme.ghost_body, bold=True))
                else:
                    gc = random.choice(self.GLITCH_CHARS)
                    gs = random.choice([
                        Style(color=theme.brand_dim, dim=True),
                        Style(color=theme.ghost_body),
                        Style(color=theme.ghost_body, bold=True),
                    ])
                    line_text.append(gc, style=gs)
            ghost.append_text(line_text)
            if line_index < len(self.GHOST) - 1:
                ghost.append("\n")
        return ghost

    def _build_brand_text(self) -> Text:
        theme = self._active_theme()
        version = get_package_version()
        brand = Text()
        brand.append("esprit", style=Style(color=theme.accent, bold=True))
        brand.append("  v" + version, style=Style(color=theme.brand_dim))
        return brand

    def _set_status(self, message: str, status_type: str = "info") -> None:
        # Cancel any pending auto-clear timer
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None

        theme = self._active_theme()
        self._status = message
        status_widget = self.query_one("#launchpad_status", Static)
        if message:
            status_widget.update(Text(message, style=Style(color=theme.status)))
        else:
            status_widget.update(Text(" ", style=Style(color=theme.status)))

        # Auto-clear success/info messages after 3 seconds
        if message and status_type in ("success", "info"):
            self._status_timer = self.set_timer(3.0, self._clear_status)

    def _clear_status(self) -> None:
        self._status_timer = None
        self._set_status("")

    def _set_view(self, view: str, push: bool = True) -> None:  # noqa: PLR0915
        if push and self._view != view:
            self._history.append(self._view)

        self._view = view
        self.selected_index = 0
        self._menu_top_row = 0

        input_widget = self.query_one("#launchpad_input", Input)
        input_widget.display = False
        input_widget.value = ""
        input_widget.password = False
        input_widget.suggester = None
        self._input_mode = None
        self._unfiltered_entries = []

        if view == "main":
            self._current_entries = self._build_main_entries()
            self._current_title = ""
            self._current_hint = "up/down to navigate  enter to select  q to quit"
        elif view == "wizard_welcome":
            self._current_entries = [
                _MenuEntry("wizard_cloud", "Log in with Esprit (Recommended)"),
                _MenuEntry("wizard_local", "Bring your own model (connectors)"),
            ]
            self._current_title = "Welcome to Esprit"
            self._current_hint = "up/down to navigate  enter to select"
        elif view == "wizard_complete":
            self._current_entries = [
                _MenuEntry("wizard_finish", "✓ You're all set! Running your first scan..."),
            ]
            self._current_title = "Setup Complete"
            self._current_hint = "press enter to continue"
        elif view == "scan_choose":
            self._current_entries = self._build_scan_target_entries()
            self._current_title = "Scan Target"
            self._current_hint = "select target type  esc to go back"
        elif view == "pre_scan":
            self._current_entries = self._build_pre_scan_entries()
            self._current_title = "Pre-scan Checks"
            self._current_hint = "review config  enter to edit/start  esc to go back"
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
            self._select_entry_by_key(f"scan_mode:{self._scan_mode}")
            self._current_title = "Scan Mode"
            self._current_hint = "quick = fast  deep = thorough  esc to go back"
        elif view == "theme":
            self._current_entries = self._build_theme_entries()
            self._select_entry_by_key(f"theme:{self._theme_id}")
            self._current_title = "Theme"
            self._current_hint = "choose a launchpad theme  esc to go back"
        elif view == "scan_target":
            self._current_entries = []
            self._current_title = "Scan Target"
            self._current_hint = "enter URL, repo, or local path  esc to go back"
            self._input_mode = "scan_target"
            input_widget.placeholder = "https://example.com, github.com/org/repo, or /path"
            input_widget.display = True
            input_widget.suggester = None
            input_widget.focus()
        elif view == "scan_local":
            self._current_entries = []
            self._current_title = "Local Path"
            self._current_hint = "tab to autocomplete  enter to use current directory  esc to go back"
            self._input_mode = "scan_local"
            input_widget.placeholder = "/path/to/project"
            self._dir_suggester.set_base_dir(self._cwd)
            input_widget.suggester = self._dir_suggester
            current_dir = str(Path(self._cwd).resolve())
            if not current_dir.endswith(os.sep):
                current_dir += os.sep
            input_widget.value = current_dir
            input_widget.cursor_position = len(current_dir)
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
        elif view == "confirm_scan":
            self._current_entries = self._build_confirm_scan_entries()
            self._current_title = "Confirm Scan"
            self._current_hint = "review and confirm  esc to go back"

        self._render_panel()

    def _select_entry_by_key(self, key: str) -> bool:
        for idx, entry in enumerate(self._current_entries):
            if entry.key == key and not self._is_non_selectable(entry):
                self.selected_index = idx
                return True
        return False

    @staticmethod
    def _shorten_hint(value: str, max_length: int = 42) -> str:
        if len(value) <= max_length:
            return value
        return value[: max_length - 3].rstrip() + "..."

    def _connected_provider_hint(self) -> str:
        rows = self._configured_provider_rows()
        if not rows:
            return "none connected"

        provider_names: list[str] = []
        for name, _auth_type, _account in rows:
            if name not in provider_names:
                provider_names.append(name)

        head = ", ".join(provider_names[:2])
        if len(provider_names) > 2:
            head = f"{head} +{len(provider_names) - 2}"
        return f"{len(provider_names)} connected: {head}"

    def _current_model_hint(self) -> str:
        model_name = Config.get("esprit_llm")
        if not model_name:
            return "not selected"
        if "/" in model_name:
            return model_name.split("/", 1)[1]
        return model_name

    def _build_main_entries(self) -> list[_MenuEntry]:
        entries: list[_MenuEntry] = []
        project_hint = self._project_name
        if self._project_type:
            project_hint += f" ({self._project_type})"

        provider_hint = self._shorten_hint(self._connected_provider_hint())
        model_hint = self._shorten_hint(f"selected: {self._current_model_hint()}")

        for option in self.MAIN_OPTIONS:
            hint = option.hint
            if option.key == "scan":
                hint = project_hint
            elif option.key == "provider":
                hint = provider_hint
            elif option.key == "model":
                hint = model_hint
            elif option.key == "scan_mode":
                hint = self._shorten_hint(f"selected: {self._scan_mode}")
            elif option.key == "theme":
                hint = self._active_theme().label
            entries.append(_MenuEntry(option.key, option.label, hint))
        return entries

    def _build_model_entries(self, filter_text: str = "") -> list[_MenuEntry]:
        current = Config.get("esprit_llm") or DEFAULT_MODEL
        entries: list[_MenuEntry] = []
        query = filter_text.lower().strip()
        models_by_provider = get_available_models()
        public_opencode_models = get_public_opencode_models(models_by_provider)
        runtime_profile = self._runtime_profile

        # Provider badges and display info
        _BADGES: dict[str, str] = {
            "esprit": "ES",
            "antigravity": "AG",
            "opencode": "OZ",
            "openai": "OAI",
            "anthropic": "CC",
            "google": "GG",
            "github-copilot": "CO",
        }
        _PROVIDER_LABELS: dict[str, str] = {
            "esprit": "ESPRIT CLOUD",
            "antigravity": "ANTIGRAVITY",
            "opencode": "OPENCODE ZEN",
            "openai": "OPENAI",
            "anthropic": "ANTHROPIC",
            "google": "GOOGLE",
            "github-copilot": "COPILOT",
        }

        def _is_provider_visible(provider_id: str) -> bool:
            if runtime_profile == "cloud":
                return provider_id == "esprit"
            return provider_id != "esprit"

        # Check which providers are connected
        connected: dict[str, bool] = {}
        for provider_id in models_by_provider:
            if not _is_provider_visible(provider_id):
                continue
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                connected[provider_id] = self._account_pool.has_accounts(provider_id)
            elif provider_id == "esprit":
                try:
                    from esprit.auth.credentials import is_authenticated
                    connected[provider_id] = is_authenticated()
                except Exception:
                    connected[provider_id] = False
            elif provider_id == "opencode":
                connected[provider_id] = self._token_store.has_credentials(provider_id) or bool(public_opencode_models)
            else:
                connected[provider_id] = self._token_store.has_credentials(provider_id)

        # Show only connected providers
        provider_order = ["esprit", "anthropic", "openai", "github-copilot", "google", "antigravity", "opencode"]
        providers_sorted = [
            provider_id for provider_id in provider_order
            if provider_id in models_by_provider and connected.get(provider_id, False)
        ]
        providers_sorted.extend(
            sorted(
                provider_id for provider_id in connected
                if provider_id not in provider_order and connected.get(provider_id, False)
            )
        )

        if not providers_sorted:
            if runtime_profile == "cloud":
                entries.append(
                    _MenuEntry(
                        "info:no_cloud_models",
                        "No Esprit Cloud models available",
                        "log in with Esprit in Provider Config",
                    )
                )
                entries.append(_MenuEntry("provider", "Open Provider Config", "connect Esprit"))
            else:
                entries.append(
                    _MenuEntry("info:no_connected_providers", "No connected providers", "open Provider Config")
                )
            entries.append(_MenuEntry("back", "\u2190 Back"))
            return entries

        for provider_id in providers_sorted:
            models = models_by_provider[provider_id]
            if provider_id == "opencode" and not self._token_store.has_credentials("opencode"):
                models = [
                    (model_id, model_name)
                    for model_id, model_name in models
                    if model_id in public_opencode_models
                ]
            badge = _BADGES.get(provider_id, provider_id[:3].upper())
            label = _PROVIDER_LABELS.get(provider_id, provider_id.upper())

            # Filter models
            matching_models = []
            for model_id, model_name in models:
                full_model = f"{provider_id}/{model_id}"
                if query and query not in model_name.lower() and query not in model_id.lower() and query not in badge.lower() and query not in label.lower():
                    continue
                matching_models.append((model_id, model_name, full_model))

            if not matching_models:
                continue

            # Provider section header
            status_hint = f"[{badge}] connected"
            if provider_id == "opencode" and not self._token_store.has_credentials("opencode"):
                status_hint = f"[{badge}] public"
            entries.append(_MenuEntry(
                f"separator:{provider_id}",
                f"\u2713 {label}",
                status_hint,
            ))

            # Model entries
            for model_id, model_name, full_model in matching_models:
                marker = "\u25cf" if full_model == current else "\u25cb"
                entries.append(_MenuEntry(
                    f"model:{full_model}",
                    f"{marker} {model_name}",
                    badge,
                ))

        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _build_provider_entries(self) -> list[_MenuEntry]:
        provider_order = ["esprit", "antigravity", "opencode", "anthropic", "openai", "google", "github-copilot"]
        entries: list[_MenuEntry] = []
        public_opencode_models = get_public_opencode_models(get_available_models())

        for provider_id in provider_order:
            provider_name = PROVIDER_NAMES.get(provider_id, provider_id)
            expired = False
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                count = self._account_pool.account_count(provider_id)
                connected = count > 0
                if connected:
                    status = f"{count} account{'s' if count != 1 else ''}"
                    accounts = self._account_pool.list_accounts(provider_id)
                    if all(a.credentials.is_expired() for a in accounts if a.enabled):
                        expired = True
                else:
                    status = "not connected"
            elif provider_id == "esprit":
                try:
                    from esprit.auth.credentials import is_authenticated as is_esprit_authenticated
                    connected = is_esprit_authenticated()
                except Exception:
                    connected = False
                status = "connected" if connected else "not connected"
            elif provider_id == "opencode":
                has_api_key = self._token_store.has_credentials(provider_id)
                connected = has_api_key or bool(public_opencode_models)
                status = "connected" if has_api_key else ("public models (no auth)" if connected else "not connected")
                if has_api_key:
                    creds = self._token_store.get(provider_id)
                    if creds and creds.is_expired():
                        expired = True
            else:
                connected = self._token_store.has_credentials(provider_id)
                status = "connected" if connected else "not connected"
                if connected:
                    creds = self._token_store.get(provider_id)
                    if creds and creds.is_expired():
                        expired = True
            marker = "\u25cf" if connected else "\u25cb"
            if expired:
                badge = " \u26a0 Expired"
            elif connected:
                badge = " \u2713 Connected"
            else:
                badge = ""
            entries.append(
                _MenuEntry(f"provider:{provider_id}", f"{marker} {provider_name}{badge}", hint=status)
            )

        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _build_provider_action_entries(self) -> list[_MenuEntry]:
        provider_id = self._selected_provider_id or ""
        entries = [_MenuEntry("provider_oauth", "Connect via OAuth")]
        if provider_id not in {"github-copilot", "esprit"}:
            entries.append(_MenuEntry("provider_api_key", "Set API Key"))
        entries.append(_MenuEntry("provider_logout", "Logout"))
        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _build_scan_mode_entries(self) -> list[_MenuEntry]:
        entries: list[_MenuEntry] = []
        descriptions = {
            "quick": "Surface-level scan (~5 min)",
            "standard": "Balanced coverage (~15 min)",
            "deep": "Comprehensive analysis (~30+ min)",
        }
        for mode in ["quick", "standard", "deep"]:
            marker = "\u25cf" if mode == self._scan_mode else "\u25cb"
            entries.append(_MenuEntry(f"scan_mode:{mode}", f"{marker} {mode.title()}", hint=descriptions[mode]))
        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _build_theme_entries(self) -> list[_MenuEntry]:
        entries: list[_MenuEntry] = []
        for theme_id, theme in self.THEMES.items():
            marker = "\u25cf" if theme_id == self._theme_id else "\u25cb"
            entries.append(_MenuEntry(f"theme:{theme_id}", f"{marker} {theme.label}", theme.hint))
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
        entries.append(_MenuEntry("scan_target_input", "Enter target", hint="URL, repo, or local path"))
        entries.append(_MenuEntry("scan_local_input", "Browse local", hint="directory autocomplete"))
        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _configured_provider_rows(self) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []

        # Esprit subscription provider (configured via `esprit provider login esprit`)
        try:
            from esprit.auth.credentials import (
                get_credentials as get_esprit_credentials,
                is_authenticated as is_esprit_authenticated,
            )

            if is_esprit_authenticated():
                creds = get_esprit_credentials() or {}
                email = str(creds.get("email") or "platform")
                rows.append(("Esprit", "Platform", email))
        except Exception:
            pass

        for provider_id in ["opencode", "openai", "anthropic", "google", "github-copilot", "antigravity"]:
            provider_name = PROVIDER_NAMES.get(provider_id, provider_id)

            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                count = self._account_pool.account_count(provider_id)
                if count <= 0:
                    continue
                best = self._account_pool.peek_best_account(provider_id)
                auth_type = "OAuth"
                account = f"{count} account{'s' if count != 1 else ''}"
                if best is not None:
                    if best.credentials.type == "api":
                        auth_type = "API Key"
                    if best.email:
                        account = best.email if count == 1 else f"{best.email} (+{count - 1})"
                rows.append((provider_name, auth_type, account))
                continue

            creds = self._token_store.get(provider_id)
            if creds is None:
                continue
            auth_type = "OAuth" if creds.type == "oauth" else "API Key"
            rows.append((provider_name, auth_type, creds.type.upper()))

        if Config.get("llm_api_key"):
            rows.append(("Direct", "API Key", "LLM_API_KEY"))

        return rows

    def _build_pre_scan_entries(self) -> list[_MenuEntry]:
        entries: list[_MenuEntry] = []
        providers = self._configured_provider_rows()

        entries.append(_MenuEntry("info:providers", "Providers"))
        if providers:
            for idx, (name, auth_type, account) in enumerate(providers, start=1):
                entries.append(
                    _MenuEntry(f"info:provider:{idx}", f"{name}", f"{auth_type} · {account}")
                )
        else:
            entries.append(
                _MenuEntry("info:no_provider", "No provider configured", "open Provider Config")
            )

        model_name = Config.get("esprit_llm")
        if model_name:
            bare_model = model_name.split("/", 1)[-1] if "/" in model_name else model_name
            entries.append(_MenuEntry("pre_model", f"Model  {bare_model}", model_name))
        else:
            entries.append(_MenuEntry("pre_model", "Model  not selected", "select a model"))

        entries.append(_MenuEntry("pre_scan_mode", f"Scan Mode  {self._scan_mode}", "change"))

        if self._pending_scan_target:
            entries.append(_MenuEntry("info:target", "Target", self._pending_scan_target))
            preview = self._target_preview(self._pending_scan_target)
            if preview:
                entries.append(_MenuEntry("info:target_preview", preview))

        entries.append(_MenuEntry("pre_start_scan", "Start Scan"))
        entries.append(_MenuEntry("back", "\u2190 Back"))
        return entries

    def _build_confirm_scan_entries(self) -> list[_MenuEntry]:
        entries: list[_MenuEntry] = []

        target = self._pending_scan_target or "not set"
        entries.append(_MenuEntry("info:confirm_target", "Target", self._shorten_hint(target, 50)))

        entries.append(_MenuEntry("info:confirm_mode", "Mode", self._scan_mode))

        rows = self._configured_provider_rows()
        provider_hint = ", ".join(name for name, _auth, _acct in rows) if rows else "none"
        entries.append(_MenuEntry("info:confirm_provider", "Provider", provider_hint))

        model_name = Config.get("esprit_llm") or "not selected"
        bare_model = model_name.split("/", 1)[-1] if "/" in model_name else model_name
        entries.append(_MenuEntry("info:confirm_model", "Model", bare_model))

        entries.append(_MenuEntry("confirm_start_scan", "\u2713 Start Scan"))
        entries.append(_MenuEntry("back", "\u2190 Go Back"))
        return entries

    @staticmethod
    def _is_non_selectable(entry: _MenuEntry) -> bool:
        return entry.key.startswith("separator:") or entry.key.startswith("info:")

    @staticmethod
    def _target_preview(target: str) -> str:
        try:
            target_type, _ = infer_target_type(target)
        except ValueError:
            return "\u274c Invalid target \u2014 please enter a URL, GitHub repo, or local directory path"
        previews = {
            "web_application": "\U0001f310 Web application \u2014 will perform HTTP reconnaissance and vulnerability scanning",
            "repository": "\U0001f4c2 GitHub repository \u2014 will clone and perform white-box source analysis",
            "local_code": "\U0001f4bb Local codebase \u2014 will analyze source code for vulnerabilities",
            "ip_address": "\U0001f310 Web application \u2014 will perform HTTP reconnaissance and vulnerability scanning",
        }
        return previews.get(target_type, "")

    def _queue_scan_target(self, target: str, replace_current_view: bool = False) -> None:
        self._pending_scan_target = target
        preview = self._target_preview(target)
        if preview:
            self._set_status(preview, "info")
        self._set_view("pre_scan", push=not replace_current_view)

    def _resolve_scan_path(self, value: str, use_cwd_if_empty: bool = False) -> str | None:
        raw = value.strip()
        if not raw:
            if not use_cwd_if_empty:
                return None
            raw = self._cwd

        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path(self._cwd) / path

        try:
            resolved = path.resolve()
        except OSError:
            return None

        if not resolved.exists():
            return None
        return str(resolved)

    def _start_scan_if_ready(self) -> None:
        if not self._configured_provider_rows():
            self._set_status("No provider configured. Connect one first.", "error")
            self._set_view("provider")
            return

        if not Config.get("esprit_llm"):
            self._set_status("No model selected. Choose one first.", "error")
            self._set_view("model")
            return

        if not self._pending_scan_target:
            self._set_status("No target selected.", "error")
            self._set_view("scan_choose")
            return

        self.exit(
            LaunchpadResult(
                action="scan",
                target=self._pending_scan_target,
                scan_mode=self._scan_mode,
                prechecked=True,
            )
        )

    def _render_panel(self) -> None:
        theme = self._active_theme()
        # Brand (on main and wizard welcome views)
        brand_widget = self.query_one("#launchpad_brand", Static)
        if self._view in ("main", "wizard_welcome"):
            brand_widget.update(self._build_brand_text())
            brand_widget.display = True
        else:
            brand_widget.display = False

        # Ghost (on main and wizard welcome views)
        ghost_widget = self.query_one("#launchpad_ghost", Static)
        if self._view in ("main", "wizard_welcome"):
            ghost_widget.display = True
            self._render_ghost()
        else:
            ghost_widget.display = False

        # Title
        title_widget = self.query_one("#launchpad_title", Static)
        if self._current_title:
            title_widget.update(Text(self._current_title, style=Style(color=theme.accent, bold=True)))
        else:
            title_widget.update(" ")
        title_widget.display = True

        # Hint
        self.query_one("#launchpad_hint", Static).update(
            Text(self._current_hint or " ", style=Style(color=theme.menu_hint, italic=True))
        )

        # Menu
        self._render_menu()

    _MENU_VISIBLE_ROWS: int = 12

    def _render_menu(self) -> None:
        theme = self._active_theme()
        menu_widget = self.query_one("#launchpad_menu", Static)
        if not self._current_entries:
            menu_widget.update(" ")
            return

        total = len(self._current_entries)
        vis = self._MENU_VISIBLE_ROWS

        # Adjust top_row so selected_index is always visible
        if self.selected_index < self._menu_top_row:
            self._menu_top_row = self.selected_index
        elif self.selected_index >= self._menu_top_row + vis:
            self._menu_top_row = self.selected_index - vis + 1
        self._menu_top_row = max(0, min(self._menu_top_row, max(0, total - vis)))

        # Determine the visible slice
        start = self._menu_top_row
        end = min(start + vis, total)

        menu_text = Text()

        # Show scroll-up indicator
        if start > 0:
            menu_text.append("  ↑ more\n", style=Style(color=theme.menu_hint, italic=True))

        for idx in range(start, end):
            entry = self._current_entries[idx]
            is_selected = idx == self.selected_index
            is_separator = entry.key.startswith("separator:")
            is_info = entry.key.startswith("info:")
            label = entry.label.strip()

            if is_separator:
                menu_text.append("  ", style=Style(color=theme.separator, bold=True))
                menu_text.append(label, style=Style(color=theme.separator, bold=True))
                if entry.hint:
                    menu_text.append(f"  {entry.hint}", style=Style(color=theme.menu_hint))
            elif is_info:
                menu_text.append("  ", style=Style(color=theme.info))
                menu_text.append(label, style=Style(color=theme.info))
                if entry.hint:
                    menu_text.append(f"  {entry.hint}", style=Style(color=theme.menu_hint))
            elif is_selected:
                prefix = "> "
                label_style = Style(color=theme.accent, bold=True)
                hint_style = Style(color=theme.selected_hint)
                menu_text.append(prefix, style=label_style)
                menu_text.append(label, style=label_style)
                if entry.hint:
                    menu_text.append(f"  {entry.hint}", style=hint_style)
            else:
                prefix = "  "
                label_style = Style(color=theme.menu_label)
                hint_style = Style(color=theme.menu_hint)
                menu_text.append(prefix, style=label_style)
                menu_text.append(label, style=label_style)
                if entry.hint:
                    menu_text.append(f"  {entry.hint}", style=hint_style)

            if idx < end - 1:
                menu_text.append("\n")

        # Show scroll-down indicator
        if end < total:
            menu_text.append(f"\n  ↓ more ({total - end})", style=Style(color=theme.menu_hint, italic=True))

        menu_widget.update(menu_text)

    # ── Actions (bound to keys via BINDINGS) ──────────────────────────

    def action_cursor_up(self) -> None:
        if self._input_mode and self._input_mode not in ("model_search", "menu_search"):
            return
        if self._current_entries:
            new_idx = (self.selected_index - 1) % len(self._current_entries)
            # Skip non-selectable entries
            attempts = len(self._current_entries)
            while self._is_non_selectable(self._current_entries[new_idx]) and attempts > 0:
                new_idx = (new_idx - 1) % len(self._current_entries)
                attempts -= 1
            self.selected_index = new_idx
            self._render_menu()

    def action_cursor_down(self) -> None:
        if self._input_mode and self._input_mode not in ("model_search", "menu_search"):
            return
        if self._current_entries:
            new_idx = (self.selected_index + 1) % len(self._current_entries)
            # Skip non-selectable entries
            attempts = len(self._current_entries)
            while self._is_non_selectable(self._current_entries[new_idx]) and attempts > 0:
                new_idx = (new_idx + 1) % len(self._current_entries)
                attempts -= 1
            self.selected_index = new_idx
            self._render_menu()

    async def action_select_entry(self) -> None:
        if self._input_mode in ("model_search", "menu_search"):
            # In search mode: enter selects the highlighted entry, not the input
            if self._current_entries and not self._is_non_selectable(
                self._current_entries[self.selected_index]
            ):
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
        if self._oauth_task is not None and not self._oauth_task.done():
            self._cancel_oauth_task()
            self._stop_oauth_timer()
            self._set_status("Authorization cancelled", "warning")
            if self._wizard_mode:
                self._set_view("wizard_welcome", push=False)
            else:
                self._set_view("provider", push=False)
            return
        if self._input_mode == "menu_search":
            # Cancel search and restore the current view
            self._set_view(self._view, push=False)
            return
        if self._input_mode:
            self._set_status("")
            self._go_back()
            return
        if self._view == "main":
            self.exit(LaunchpadResult(action="exit", scan_mode=self._scan_mode))
        else:
            self._go_back()

    def action_quit_app(self) -> None:
        self._cancel_oauth_task()
        self._stop_oauth_timer()
        self.exit(LaunchpadResult(action="exit", scan_mode=self._scan_mode))

    def action_start_search(self) -> None:
        """Activate search/filter mode for the current menu view."""
        if self._input_mode is not None:
            return
        if not self._current_entries:
            return
        self._unfiltered_entries = list(self._current_entries)
        self._input_mode = "menu_search"
        input_widget = self.query_one("#launchpad_input", Input)
        input_widget.value = ""
        input_widget.placeholder = "search..."
        input_widget.password = False
        input_widget.suggester = None
        input_widget.display = True
        input_widget.focus()
        self._current_hint = "type to filter  up/down to navigate  enter to select  esc to cancel"
        self._render_panel()

    # ── Entry activation ──────────────────────────────────────────────

    async def _activate_entry(self, entry: _MenuEntry) -> None:  # noqa: PLR0911, PLR0912
        key = entry.key

        if key.startswith("info:"):
            return

        # Wizard entries
        if key == "wizard_cloud":
            self._selected_provider_id = "esprit"
            self._set_runtime_profile("cloud")
            configured_model = (Config.get("esprit_llm") or "").strip().lower()
            if not configured_model.startswith("esprit/"):
                os.environ["ESPRIT_LLM"] = "esprit/default"
                Config.save_current()
            await self._connect_selected_provider()
            return
        if key == "wizard_local":
            self._set_runtime_profile("connectors")
            configured_model = (Config.get("esprit_llm") or "").strip().lower()
            if configured_model.startswith("esprit/") or configured_model.startswith("bedrock/"):
                os.environ["ESPRIT_LLM"] = ""
                Config.save_current()
            self._mark_wizard_done()
            self._wizard_mode = False
            self._set_view("provider")
            self._set_status("Connect a provider to continue", "info")
            return
        if key == "wizard_finish":
            self._mark_wizard_done()
            self._wizard_mode = False
            self._set_view("scan_choose", push=False)
            return

        if key == "model":
            self._set_view("model")
            return
        if key == "provider":
            self._set_view("provider")
            return
        if key == "scan_mode":
            self._set_view("scan_mode")
            return
        if key == "theme":
            self._set_view("theme")
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
            model_name_lower = model_name.lower()
            if model_name_lower.startswith("esprit/") or model_name_lower.startswith("bedrock/"):
                self._set_runtime_profile("cloud")
            else:
                self._set_runtime_profile("connectors")
            os.environ["ESPRIT_LLM"] = model_name
            Config.save_current()
            self._set_status(f"Model set: {model_name}", "success")
            if self._history and self._history[-1] == "pre_scan":
                self._set_view("pre_scan", push=False)
            else:
                self._set_view("model", push=False)
            return

        if key.startswith("scan_mode:"):
            mode = key.split(":", 1)[1]
            self._scan_mode = mode
            self._set_status(f"Scan mode: {mode}", "success")
            if self._history and self._history[-1] == "pre_scan":
                self._set_view("pre_scan", push=False)
            else:
                self._set_view("scan_mode", push=False)
            return

        if key.startswith("theme:"):
            theme_id = key.split(":", 1)[1]
            self._set_theme(theme_id, persist=True)
            self._set_view("theme", push=False)
            return

        if key == "scan_cwd":
            self._queue_scan_target(self._cwd)
            return
        if key == "scan_target_input":
            self._set_view("scan_target")
            return
        if key == "scan_local_input":
            self._set_view("scan_local")
            return
        if key == "pre_model":
            self._set_view("model")
            return
        if key == "pre_scan_mode":
            self._set_view("scan_mode")
            return
        if key == "pre_start_scan":
            # Validate before showing confirmation
            if not self._configured_provider_rows():
                self._set_status("No provider configured. Connect one first.", "error")
                self._set_view("provider")
                return
            if not Config.get("esprit_llm"):
                self._set_status("No model selected. Choose one first.", "error")
                self._set_view("model")
                return
            if not self._pending_scan_target:
                self._set_status("No target selected.", "error")
                self._set_view("scan_choose")
                return
            self._set_view("confirm_scan")
            return
        if key == "confirm_start_scan":
            self._start_scan_if_ready()
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
                    self._set_status(f"Removed {len(accounts)} account(s) from {PROVIDER_NAMES.get(provider_id, provider_id)}", "success")
                else:
                    self._set_status("No credentials to remove", "warning")
            elif provider_id == "esprit":
                try:
                    from esprit.auth.credentials import (
                        clear_credentials,
                        is_authenticated as is_esprit_authenticated,
                    )

                    if is_esprit_authenticated():
                        clear_credentials()
                        self._token_store.delete("esprit")
                        self._set_status(f"Logged out from {PROVIDER_NAMES.get(provider_id, provider_id)}", "success")
                    else:
                        self._set_status("No credentials to remove", "warning")
                except Exception:
                    self._set_status("Failed to clear Esprit credentials", "error")
            elif self._token_store.delete(provider_id):
                self._set_status(f"Logged out from {PROVIDER_NAMES.get(provider_id, provider_id)}", "success")
            else:
                self._set_status("No credentials to remove", "warning")
            self._set_view("provider", push=False)

    def _go_back(self) -> None:
        if not self._history:
            self._set_view("main", push=False)
            return
        previous = self._history.pop()
        self._set_view(previous, push=False)

    # ── OAuth flow ────────────────────────────────────────────────────

    def _start_oauth_timer(self, provider_name: str) -> None:
        self._stop_oauth_timer()
        self._oauth_start_time = time.monotonic()
        self._oauth_provider_name = provider_name
        self._oauth_timer = self.set_interval(1.0, self._tick_oauth_status)

    def _stop_oauth_timer(self) -> None:
        if self._oauth_timer is not None:
            self._oauth_timer.stop()
            self._oauth_timer = None

    def _cancel_oauth_task(self) -> None:
        if self._oauth_task is not None and not self._oauth_task.done():
            self._oauth_task.cancel()
        self._oauth_task = None

    def _tick_oauth_status(self) -> None:
        elapsed = int(time.monotonic() - self._oauth_start_time)
        self._set_status(f"⏳ Waiting for browser authorization... ({elapsed}s elapsed)", "info")

    async def _run_provider_auto_callback(
        self,
        provider_id: str,
        provider_impl: Any,
        auth_result: Any,
    ) -> None:
        try:
            callback_result = await provider_impl.callback(auth_result)
        except asyncio.CancelledError:
            return
        except Exception as exc:  # noqa: BLE001
            callback_result = AuthCallbackResult(success=False, error=str(exc))
        finally:
            self._stop_oauth_timer()

        self._oauth_task = None
        await self._handle_provider_callback(provider_id, callback_result)

    async def _connect_selected_provider(self) -> None:
        provider_id = self._selected_provider_id
        if not provider_id:
            return

        if self._oauth_task is not None and not self._oauth_task.done():
            self._set_status("Authorization already in progress", "warning")
            return

        provider = get_provider_auth(provider_id)
        if not provider:
            self._set_status("Provider not available", "error")
            return

        if provider_id == "esprit":
            try:
                from esprit.auth.credentials import is_authenticated
                if is_authenticated():
                    self._set_status("Already logged in with Esprit", "success")
                    if self._wizard_mode:
                        self._set_view("wizard_complete", push=False)
                    else:
                        self._set_view("provider", push=False)
                    return
            except Exception:
                pass

        provider_name = PROVIDER_NAMES.get(provider_id, provider_id)
        self._set_status(f"Starting OAuth for {provider_name}...", "info")

        provider_impl: Any = provider

        try:
            auth_result = await provider_impl.authorize()
        except Exception as exc:  # noqa: BLE001
            self._set_status(_friendly_login_error(exc), "error")
            return

        try:
            opened = await asyncio.to_thread(webbrowser.open, auth_result.url)
        except Exception:
            opened = False
        if not opened:
            self._set_status(f"Open manually: {auth_result.url}", "warning")
        else:
            self._set_status(f"Browser opened for {provider_name}", "info")

        if auth_result.method == AuthMethod.CODE:
            self._pending_auth = (provider_id, provider, auth_result)
            self._set_view("provider_code")
            return

        self._start_oauth_timer(provider_name)
        self._oauth_task = asyncio.create_task(
            self._run_provider_auto_callback(provider_id, provider_impl, auth_result)
        )

    async def _handle_provider_callback(self, provider_id: str, callback_result: Any) -> None:
        self._stop_oauth_timer()
        self._pending_auth = None
        if not callback_result.success:
            self._set_status(
                _friendly_login_error(callback_result.error)
                if isinstance(callback_result.error, Exception)
                else f"Login failed: {callback_result.error}. Please try again or use a different provider.",
                "error",
            )
            if self._wizard_mode:
                self._set_view("wizard_welcome", push=False)
            else:
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
                # Esprit stores platform credentials outside provider token store.
                if provider_id != "esprit":
                    self._token_store.set(provider_id, callback_result.credentials)
        if provider_id == "esprit":
            self._set_runtime_profile("cloud")
        else:
            self._set_runtime_profile("connectors")
        self._set_status(f"Connected {PROVIDER_NAMES.get(provider_id, provider_id)}", "success")
        if self._wizard_mode:
            self._set_view("wizard_complete", push=False)
        else:
            self._set_view("provider", push=False)

    # ── Input change (live search) ─────────────────────────────────────

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._input_mode == "model_search":
            self._model_filter = event.value
            self._current_entries = self._build_model_entries(self._model_filter)
            self.selected_index = 0
            # Skip non-selectable entries to first selectable entry
            while (
                self.selected_index < len(self._current_entries)
                and self._is_non_selectable(self._current_entries[self.selected_index])
            ):
                self.selected_index += 1
            self._render_menu()
        elif self._input_mode == "menu_search":
            query = event.value.lower().strip()
            if query:
                self._current_entries = [
                    e for e in self._unfiltered_entries
                    if query in e.label.lower() or query in e.hint.lower()
                ]
            else:
                self._current_entries = list(self._unfiltered_entries)
            self.selected_index = 0
            # Skip non-selectable entries to first selectable entry
            while (
                self.selected_index < len(self._current_entries)
                and self._is_non_selectable(self._current_entries[self.selected_index])
            ):
                self.selected_index += 1
            if self.selected_index >= len(self._current_entries):
                self.selected_index = 0
            self._render_menu()

    async def on_input_submitted(self, event: Input.Submitted) -> None:  # noqa: PLR0911
        value = event.value.strip()

        if self._input_mode == "scan_target":
            if not value:
                self._set_status("Target is required", "error")
                return
            resolved_local = self._resolve_scan_path(value)
            target = resolved_local or value
            self._queue_scan_target(target, replace_current_view=True)
            return

        if self._input_mode == "scan_local":
            resolved = self._resolve_scan_path(value, use_cwd_if_empty=True)
            if not resolved:
                attempted = value or self._cwd
                self._set_status(f"Path not found: {attempted}", "error")
                return
            self._queue_scan_target(resolved, replace_current_view=True)
            return

        if self._input_mode == "provider_api_key":
            provider_id = self._selected_provider_id
            if not provider_id:
                self._set_status("No provider selected", "error")
                self._go_back()
                return
            if not value:
                self._set_status("API key cannot be empty", "error")
                return

            creds = OAuthCredentials(type="api", access_token=value)
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                self._account_pool.add_account(provider_id, creds, f"api-key-{self._account_pool.account_count(provider_id) + 1}")
            else:
                self._token_store.set(provider_id, creds)
            self._set_status(f"Saved API key for {PROVIDER_NAMES.get(provider_id, provider_id)}", "success")
            if self._wizard_mode:
                self._set_view("wizard_complete", push=False)
            else:
                self._set_view("provider", push=False)
            return

        if self._input_mode == "provider_code":
            pending = self._pending_auth
            if not pending:
                self._set_status("No pending authorization", "error")
                self._set_view("provider", push=False)
                return
            if not value:
                self._set_status("Code cannot be empty", "error")
                return

            provider_id, provider, auth_result = pending
            provider_impl: Any = provider
            provider_name = PROVIDER_NAMES.get(provider_id, provider_id)
            self._start_oauth_timer(provider_name)
            callback_result = await provider_impl.callback(auth_result, value)
            self._stop_oauth_timer()
            self._pending_auth = None
            await self._handle_provider_callback(provider_id, callback_result)


async def run_launchpad() -> LaunchpadResult | None:
    app = LaunchpadApp()
    return await app.run_async()
