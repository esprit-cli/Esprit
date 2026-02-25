import os
import webbrowser
from dataclasses import dataclass
from typing import Any, ClassVar

from rich.style import Style
from rich.text import Text
from textual.app import App, ComposeResult, ScreenStackError
from textual.binding import Binding
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Input, Static

from esprit.config import Config, save_current_config
from esprit.llm.config import DEFAULT_MODEL
from esprit.providers import PROVIDER_NAMES, get_provider_auth
from esprit.providers.account_pool import get_account_pool
from esprit.providers.base import AuthMethod, OAuthCredentials
from esprit.providers.config import get_available_models, get_public_opencode_models
from esprit.providers.constants import MULTI_ACCOUNT_PROVIDERS as _MULTI_ACCOUNT_PROVIDERS
from esprit.providers.token_store import TokenStore


@dataclass(slots=True)
class OnboardingResult:
    action: str


@dataclass(slots=True)
class _MenuEntry:
    key: str
    label: str
    hint: str = ""


@dataclass(frozen=True, slots=True)
class _ThemeOption:
    key: str
    label: str
    hint: str
    accent: str
    ghost: str
    ghost_eye: str
    text: str
    dim: str
    status: str


class OnboardingApp(App[OnboardingResult | None]):  # type: ignore[misc]
    CSS_PATH = "assets/onboarding_styles.tcss"
    DEFAULT_THEME = "esprit"

    BINDINGS: ClassVar[list[Binding]] = [  # type: ignore[assignment]
        Binding("up", "cursor_up", "Up", show=False, priority=True),
        Binding("down", "cursor_down", "Down", show=False, priority=True),
        Binding("enter", "select_entry", "Select", show=False, priority=True),
        Binding("escape", "go_back", "Back", show=False, priority=True),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("ctrl+c", "quit_app", "Quit", show=False, priority=True),
        Binding("ctrl+q", "quit_app", "Quit", show=False),
    ]

    THEMES: ClassVar[dict[str, _ThemeOption]] = {
        "esprit": _ThemeOption(
            "esprit",
            "Esprit",
            "Neon cyan + noir",
            "#22d3ee",
            "#22d3ee",
            "#0a0a0a",
            "#d6f9ff",
            "#6b7f85",
            "#7cb9c4",
        ),
        "ember": _ThemeOption(
            "ember",
            "Ember",
            "Molten amber + charcoal",
            "#f97316",
            "#fb923c",
            "#1c140f",
            "#ffd8be",
            "#9d7359",
            "#cf9168",
        ),
        "matrix": _ThemeOption(
            "matrix",
            "Matrix",
            "Signal green + black",
            "#22c55e",
            "#22c55e",
            "#04120a",
            "#d6ffe3",
            "#5f8a67",
            "#7ec18f",
        ),
        "glacier": _ThemeOption(
            "glacier",
            "Glacier",
            "Ice blue + deep navy",
            "#38bdf8",
            "#38bdf8",
            "#04131c",
            "#c8ecff",
            "#5a7f95",
            "#8fb7d1",
        ),
        "crt": _ThemeOption(
            "crt",
            "CRT",
            "Phosphor green + scanlines",
            "#33ff33",
            "#33ff33",
            "#001400",
            "#dfffdc",
            "#5f8e5f",
            "#7cb07c",
        ),
        "sakura": _ThemeOption(
            "sakura",
            "Sakura",
            "Cherry pink + plum",
            "#f472b6",
            "#f472b6",
            "#2a0f1f",
            "#ffd8ea",
            "#a16c87",
            "#d49ebd",
        ),
    }

    PROVIDER_ORDER: ClassVar[list[str]] = [
        "esprit",
        "antigravity",
        "opencode",
        "openai",
        "anthropic",
        "google",
        "github-copilot",
    ]

    GHOST_FRAMES: ClassVar[list[tuple[str, ...]]] = [
        (
            "           .-''''-.",
            "         .'  .-.  '.",
            "        /   (o o)   \\",
            "       |     ^       |",
            "       |  \\_____/    |",
            "       |             |",
            "       |  .-'''-.    |",
            "        \\_/     \\___/",
        ),
        (
            "           .-''''-.",
            "         .'  .-.  '.",
            "        /   (o o)   \\",
            "       |     -       |",
            "       |  \\_____/    |",
            "       |             |",
            "       |  .-'''-.    |",
            "        \\_/     \\___/",
        ),
    ]

    selected_index: reactive[int] = reactive(0)

    def __init__(self) -> None:
        super().__init__()
        self._token_store = TokenStore()
        self._account_pool = get_account_pool()
        self._view = "welcome"
        self._history: list[str] = []
        self._current_entries: list[_MenuEntry] = []
        self._current_title = ""
        self._current_hint = ""
        self._status = ""
        self._input_mode: str | None = None
        self._selected_provider_id: str | None = None
        self._pending_auth: tuple[str, Any, Any] | None = None
        self._model_filter = ""
        self._animation_step = 0
        self._ghost_timer: Any | None = None
        self._theme_id = self._normalize_theme_id(Config.get_launchpad_theme())

    def compose(self) -> ComposeResult:
        yield Vertical(
            Static("", id="onboarding_ghost"),
            Static("", id="onboarding_brand"),
            Static("", id="onboarding_title"),
            Static("", id="onboarding_menu"),
            Input(placeholder="", id="onboarding_input"),
            Static("", id="onboarding_status"),
            Static("", id="onboarding_hint"),
            id="onboarding_root",
        )

    def on_mount(self) -> None:
        self.title = "esprit onboarding"
        self._apply_theme_class()
        input_widget = self.query_one("#onboarding_input", Input)
        input_widget.display = False
        self._set_view("welcome", push=False)
        self._ghost_timer = self.set_interval(0.35, self._tick_animation)

    def on_unmount(self) -> None:
        if self._ghost_timer is not None:
            self._ghost_timer.stop()

    def _tick_animation(self) -> None:
        self._animation_step += 1
        self._render_ghost()

    def _normalize_theme_id(self, theme_id: str | None) -> str:
        if theme_id and theme_id in self.THEMES:
            return theme_id
        return self.DEFAULT_THEME

    def _active_theme(self) -> _ThemeOption:
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

    def _set_theme(self, theme_id: str) -> None:
        next_theme = self._normalize_theme_id(theme_id)
        changed = next_theme != self._theme_id
        self._theme_id = next_theme
        self._apply_theme_class()
        if changed:
            Config.save_launchpad_theme(next_theme)
            self._set_status(f"Theme set: {self._active_theme().label}")
        self._render_panel()

    def _set_status(self, message: str) -> None:
        self._status = message
        theme = self._active_theme()
        self.query_one("#onboarding_status", Static).update(
            Text(message or " ", style=Style(color=theme.status))
        )

    def _build_brand_text(self) -> Text:
        theme = self._active_theme()
        brand = Text()
        brand.append("Welcome to ", style=Style(color=theme.dim))
        brand.append("ESPRIT", style=Style(color=theme.accent, bold=True))
        brand.append(" setup", style=Style(color=theme.dim))
        return brand

    def _build_ghost_text(self, phase: int) -> Text:
        frame = self.GHOST_FRAMES[phase % len(self.GHOST_FRAMES)]
        theme = self._active_theme()
        text = Text()
        for line_index, line in enumerate(frame):
            line_text = Text()
            for char in line:
                if char == "o":
                    line_text.append(char, style=Style(color=theme.ghost_eye, bold=True))
                elif char.strip():
                    line_text.append(char, style=Style(color=theme.ghost))
                else:
                    line_text.append(" ")
            text.append_text(line_text)
            if line_index < len(frame) - 1:
                text.append("\n")
        return text

    def _render_ghost(self) -> None:
        self.query_one("#onboarding_ghost", Static).update(
            self._build_ghost_text(self._animation_step)
        )

    def _set_view(self, view: str, push: bool = True) -> None:  # noqa: PLR0915
        if push and self._view != view:
            self._history.append(self._view)
        self._view = view
        self.selected_index = 0

        input_widget = self.query_one("#onboarding_input", Input)
        input_widget.display = False
        input_widget.value = ""
        input_widget.password = False
        self._input_mode = None

        if view == "welcome":
            self._current_title = "First-Time Onboarding"
            self._current_hint = "set up theme, provider, and model  enter to continue"
            self._current_entries = [
                _MenuEntry("welcome_start", "Start setup", "recommended"),
                _MenuEntry("welcome_skip", "Skip for now", "you can run onboarding next command"),
            ]
        elif view == "theme":
            self._current_title = "Theme"
            self._current_hint = "choose your visual style"
            self._current_entries = self._build_theme_entries()
            self._select_entry_by_key(f"theme:{self._theme_id}")
        elif view == "provider":
            self._current_title = "Connect Providers"
            self._current_hint = "connect at least one provider for model access"
            self._current_entries = self._build_provider_entries()
        elif view == "provider_actions":
            provider_name = PROVIDER_NAMES.get(self._selected_provider_id or "", "Provider")
            self._current_title = provider_name
            self._current_hint = "connect or manage credentials"
            self._current_entries = self._build_provider_action_entries()
        elif view == "provider_api_key":
            self._current_title = "Provider API Key"
            self._current_hint = "paste API key and press enter"
            self._current_entries = []
            self._input_mode = "provider_api_key"
            input_widget.placeholder = "sk-..."
            input_widget.password = True
            input_widget.display = True
            input_widget.focus()
        elif view == "provider_code":
            self._current_title = "OAuth Code"
            self._current_hint = "paste the code from your browser"
            self._current_entries = []
            self._input_mode = "provider_code"
            input_widget.placeholder = "authorization code"
            input_widget.display = True
            input_widget.focus()
        elif view == "model":
            self._current_title = "Preferred Model"
            self._current_hint = "type to search models  enter to select"
            self._current_entries = self._build_model_entries()
            self._input_mode = "model_search"
            input_widget.placeholder = "search models..."
            input_widget.display = True
            input_widget.focus()
        else:  # done
            self._current_title = "Setup Complete"
            self._current_hint = "finish and continue to launchpad"
            self._current_entries = self._build_done_entries()

        self._render_panel()

    def _configured_provider_rows(self) -> list[tuple[str, str, str]]:
        rows: list[tuple[str, str, str]] = []
        try:
            from esprit.auth.credentials import (
                get_credentials as get_esprit_credentials,
                is_authenticated as is_esprit_authenticated,
            )

            if is_esprit_authenticated():
                creds = get_esprit_credentials() or {}
                email = str(creds.get("email") or "platform")
                rows.append(("Esprit", "Platform", email))
        except Exception:  # noqa: BLE001
            pass

        for provider_id in [
            "opencode",
            "openai",
            "anthropic",
            "google",
            "github-copilot",
            "antigravity",
        ]:
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

    def _build_theme_entries(self) -> list[_MenuEntry]:
        entries: list[_MenuEntry] = []
        for theme_id, theme in self.THEMES.items():
            marker = "●" if theme_id == self._theme_id else "○"
            entries.append(_MenuEntry(f"theme:{theme_id}", f"{marker} {theme.label}", theme.hint))
        entries.append(_MenuEntry("theme_continue", "Continue", "next: providers"))
        entries.append(_MenuEntry("back", "← Back"))
        return entries

    def _provider_connected_hint(self, provider_id: str) -> tuple[bool, str]:
        if provider_id in _MULTI_ACCOUNT_PROVIDERS:
            count = self._account_pool.account_count(provider_id)
            return (
                count > 0,
                f"{count} account{'s' if count != 1 else ''}" if count > 0 else "not connected",
            )
        if provider_id == "esprit":
            try:
                from esprit.auth.credentials import is_authenticated as is_esprit_authenticated

                connected = is_esprit_authenticated()
                return (connected, "connected" if connected else "not connected")
            except Exception:  # noqa: BLE001
                return (False, "not connected")
        if provider_id == "opencode":
            public_models = get_public_opencode_models(get_available_models())
            has_api_key = self._token_store.has_credentials("opencode")
            if has_api_key:
                return (True, "connected")
            if public_models:
                return (True, "public models (no auth)")
            return (False, "not connected")
        connected = self._token_store.has_credentials(provider_id)
        return (connected, "connected" if connected else "not connected")

    def _build_provider_entries(self) -> list[_MenuEntry]:
        entries: list[_MenuEntry] = []
        for provider_id in self.PROVIDER_ORDER:
            provider_name = PROVIDER_NAMES.get(provider_id, provider_id)
            connected, hint = self._provider_connected_hint(provider_id)
            marker = "●" if connected else "○"
            entries.append(_MenuEntry(f"provider:{provider_id}", f"{marker} {provider_name}", hint))

        rows = self._configured_provider_rows()
        summary = f"{len(rows)} configured" if rows else "none configured"
        entries.append(_MenuEntry("provider_continue", "Continue", summary))
        entries.append(_MenuEntry("back", "← Back"))
        return entries

    def _build_provider_action_entries(self) -> list[_MenuEntry]:
        provider_id = self._selected_provider_id or ""
        entries = [_MenuEntry("provider_oauth", "Connect via OAuth")]
        if provider_id not in {"github-copilot", "esprit"}:
            entries.append(_MenuEntry("provider_api_key", "Set API Key"))
        entries.append(_MenuEntry("provider_logout", "Logout"))
        entries.append(_MenuEntry("back", "← Back"))
        return entries

    def _build_model_entries(self, filter_text: str = "") -> list[_MenuEntry]:
        current_model = Config.get("esprit_llm") or DEFAULT_MODEL
        query = filter_text.lower().strip()
        entries: list[_MenuEntry] = []
        models_by_provider = get_available_models()
        public_opencode_models = get_public_opencode_models(models_by_provider)

        connected_provider_ids: list[str] = []
        for provider_id in models_by_provider:
            connected, _ = self._provider_connected_hint(provider_id)
            if connected:
                connected_provider_ids.append(provider_id)

        for provider_id in sorted(connected_provider_ids):
            models = models_by_provider[provider_id]
            if provider_id == "opencode" and not self._token_store.has_credentials("opencode"):
                models = [
                    (model_id, model_name)
                    for model_id, model_name in models
                    if model_id in public_opencode_models
                ]
            if not models:
                continue

            matching_models: list[tuple[str, str, str]] = []
            for model_id, model_name in models:
                full_model = f"{provider_id}/{model_id}"
                if (
                    query
                    and query not in model_name.lower()
                    and query not in model_id.lower()
                    and query not in provider_id.lower()
                ):
                    continue
                matching_models.append((model_id, model_name, full_model))

            if not matching_models:
                continue

            provider_name = PROVIDER_NAMES.get(provider_id, provider_id)
            entries.append(
                _MenuEntry(f"info:provider:{provider_id}", provider_name.upper(), "models")
            )

            for _model_id, model_name, full_model in matching_models:
                marker = "●" if full_model == current_model else "○"
                entries.append(
                    _MenuEntry(f"model:{full_model}", f"{marker} {model_name}", full_model)
                )

        if not any(not entry.key.startswith("info:") for entry in entries):
            entries.append(
                _MenuEntry("info:no_model", "No models available", "connect a provider first")
            )

        entries.append(_MenuEntry("model_continue", "Continue", "next: finish"))
        entries.append(_MenuEntry("back", "← Back"))
        return entries

    def _build_done_entries(self) -> list[_MenuEntry]:
        model = Config.get("esprit_llm") or "not selected"
        providers = self._configured_provider_rows()
        entries = [
            _MenuEntry("info:theme", f"Theme: {self._active_theme().label}"),
            _MenuEntry("info:providers", f"Providers: {len(providers)} connected"),
            _MenuEntry("info:model", f"Model: {model}"),
            _MenuEntry("done_finish", "Finish onboarding", "open launchpad"),
            _MenuEntry("done_skip", "Skip for now", "continue with current command"),
        ]
        return entries

    @staticmethod
    def _is_non_selectable(entry: _MenuEntry) -> bool:
        return entry.key.startswith("info:")

    def _select_entry_by_key(self, key: str) -> bool:
        for idx, entry in enumerate(self._current_entries):
            if entry.key == key and not self._is_non_selectable(entry):
                self.selected_index = idx
                return True
        return False

    def _render_panel(self) -> None:
        theme = self._active_theme()
        self.query_one("#onboarding_brand", Static).update(self._build_brand_text())
        self._render_ghost()
        self.query_one("#onboarding_title", Static).update(
            Text(self._current_title, style=Style(color=theme.accent, bold=True))
        )
        self.query_one("#onboarding_hint", Static).update(
            Text(self._current_hint or " ", style=Style(color=theme.dim, italic=True))
        )
        self._render_menu()

    def _render_menu(self) -> None:
        theme = self._active_theme()
        menu_text = Text()
        for idx, entry in enumerate(self._current_entries):
            selected = idx == self.selected_index
            if self._is_non_selectable(entry):
                menu_text.append("  ", style=Style(color=theme.dim))
                menu_text.append(entry.label, style=Style(color=theme.dim))
                if entry.hint:
                    menu_text.append(f"  {entry.hint}", style=Style(color=theme.dim))
            elif selected:
                menu_text.append("> ", style=Style(color=theme.accent, bold=True))
                menu_text.append(entry.label, style=Style(color=theme.accent, bold=True))
                if entry.hint:
                    menu_text.append(f"  {entry.hint}", style=Style(color=theme.text))
            else:
                menu_text.append("  ", style=Style(color=theme.text))
                menu_text.append(entry.label, style=Style(color=theme.text))
                if entry.hint:
                    menu_text.append(f"  {entry.hint}", style=Style(color=theme.dim))
            if idx < len(self._current_entries) - 1:
                menu_text.append("\n")
        self.query_one("#onboarding_menu", Static).update(menu_text if menu_text else " ")

    def action_cursor_up(self) -> None:
        if self._input_mode and self._input_mode != "model_search":
            return
        if not self._current_entries:
            return
        new_idx = (self.selected_index - 1) % len(self._current_entries)
        attempts = len(self._current_entries)
        while self._is_non_selectable(self._current_entries[new_idx]) and attempts > 0:
            new_idx = (new_idx - 1) % len(self._current_entries)
            attempts -= 1
        self.selected_index = new_idx
        self._render_menu()

    def action_cursor_down(self) -> None:
        if self._input_mode and self._input_mode != "model_search":
            return
        if not self._current_entries:
            return
        new_idx = (self.selected_index + 1) % len(self._current_entries)
        attempts = len(self._current_entries)
        while self._is_non_selectable(self._current_entries[new_idx]) and attempts > 0:
            new_idx = (new_idx + 1) % len(self._current_entries)
            attempts -= 1
        self.selected_index = new_idx
        self._render_menu()

    async def action_select_entry(self) -> None:
        if self._input_mode == "model_search":
            if self._current_entries and not self._is_non_selectable(
                self._current_entries[self.selected_index]
            ):
                await self._activate_entry(self._current_entries[self.selected_index])
            return
        if self._input_mode:
            input_widget = self.query_one("#onboarding_input", Input)
            await input_widget.action_submit()
            return
        if self._current_entries:
            await self._activate_entry(self._current_entries[self.selected_index])

    def action_go_back(self) -> None:
        if self._input_mode:
            self._go_back()
            return
        if self._view == "welcome":
            self.exit(OnboardingResult(action="exit"))
            return
        self._go_back()

    def action_quit_app(self) -> None:
        self.exit(OnboardingResult(action="exit"))

    def _go_back(self) -> None:
        if not self._history:
            self._set_view("welcome", push=False)
            return
        previous = self._history.pop()
        self._set_view(previous, push=False)

    async def _activate_entry(self, entry: _MenuEntry) -> None:  # noqa: PLR0911, PLR0912
        key = entry.key
        if key == "welcome_start":
            self._set_view("theme")
            return
        if key == "welcome_skip":
            self.exit(OnboardingResult(action="skipped"))
            return
        if key.startswith("theme:"):
            self._set_theme(key.split(":", 1)[1])
            self._set_view("theme", push=False)
            return
        if key == "theme_continue":
            self._set_view("provider")
            return
        if key.startswith("provider:"):
            self._selected_provider_id = key.split(":", 1)[1]
            self._set_view("provider_actions")
            return
        if key == "provider_continue":
            self._set_view("model")
            return
        if key == "provider_oauth":
            await self._connect_selected_provider()
            return
        if key == "provider_api_key":
            self._set_view("provider_api_key")
            return
        if key == "provider_logout":
            self._logout_selected_provider()
            self._set_view("provider", push=False)
            return
        if key.startswith("model:"):
            model_name = key.split(":", 1)[1]
            os.environ["ESPRIT_LLM"] = model_name
            save_current_config()
            self._set_status(f"Model set: {model_name}")
            self._set_view("model", push=False)
            return
        if key == "model_continue":
            self._set_view("done")
            return
        if key == "done_finish":
            self.exit(OnboardingResult(action="completed"))
            return
        if key == "done_skip":
            self.exit(OnboardingResult(action="skipped"))
            return
        if key == "back":
            self._go_back()

    def _logout_selected_provider(self) -> None:
        provider_id = self._selected_provider_id
        if not provider_id:
            return
        if provider_id in _MULTI_ACCOUNT_PROVIDERS:
            accounts = self._account_pool.list_accounts(provider_id)
            for acct in accounts:
                self._account_pool.remove_account(provider_id, acct.email)
            self._set_status(
                f"Removed {len(accounts)} account(s)" if accounts else "No credentials to remove"
            )
            return
        if provider_id == "esprit":
            try:
                from esprit.auth.credentials import clear_credentials, is_authenticated

                if is_authenticated():
                    clear_credentials()
                    self._set_status("Logged out from Esprit")
                else:
                    self._set_status("No credentials to remove")
            except Exception:  # noqa: BLE001
                self._set_status("Failed to clear Esprit credentials")
            return
        if self._token_store.delete(provider_id):
            self._set_status(f"Logged out from {PROVIDER_NAMES.get(provider_id, provider_id)}")
        else:
            self._set_status("No credentials to remove")

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

        callback_result = await provider_impl.callback(auth_result)
        await self._handle_provider_callback(provider_id, callback_result)

    async def _handle_provider_callback(self, provider_id: str, callback_result: Any) -> None:
        if not callback_result.success:
            self._set_status(f"Login failed: {callback_result.error}")
            self._set_view("provider", push=False)
            return

        if callback_result.credentials:
            if provider_id in _MULTI_ACCOUNT_PROVIDERS:
                email = (
                    callback_result.credentials.extra.get("email", "unknown")
                    if callback_result.credentials.extra
                    else "unknown"
                )
                if not email or email == "unknown":
                    email = callback_result.credentials.account_id or (
                        f"account-{self._account_pool.account_count(provider_id) + 1}"
                    )
                if callback_result.credentials.extra is None:
                    callback_result.credentials.extra = {}
                callback_result.credentials.extra["email"] = email
                self._account_pool.add_account(provider_id, callback_result.credentials, email)
            else:
                if provider_id != "esprit":
                    self._token_store.set(provider_id, callback_result.credentials)

        self._set_status(f"Connected {PROVIDER_NAMES.get(provider_id, provider_id)}")
        self._set_view("provider", push=False)

    def on_input_changed(self, event: Input.Changed) -> None:
        if self._input_mode != "model_search":
            return
        self._model_filter = event.value
        self._current_entries = self._build_model_entries(self._model_filter)
        self.selected_index = 0
        while (
            self.selected_index < len(self._current_entries)
            and self._is_non_selectable(self._current_entries[self.selected_index])
        ):
            self.selected_index += 1
        if self.selected_index >= len(self._current_entries):
            self.selected_index = 0
        self._render_menu()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
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
                self._account_pool.add_account(
                    provider_id,
                    creds,
                    f"api-key-{self._account_pool.account_count(provider_id) + 1}",
                )
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
            callback_result = await provider_impl.callback(auth_result, value)
            self._pending_auth = None
            await self._handle_provider_callback(provider_id, callback_result)


async def run_onboarding() -> OnboardingResult | None:
    app = OnboardingApp()
    return await app.run_async()
