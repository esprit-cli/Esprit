import contextlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Config:
    """Configuration Manager for Esprit."""

    # LLM Configuration
    esprit_llm = None
    llm_api_key = None
    llm_api_base = None
    openai_api_base = None
    litellm_base_url = None
    ollama_api_base = None
    esprit_reasoning_effort = "high"
    esprit_llm_max_retries = "5"
    esprit_memory_compressor_timeout = "30"
    llm_timeout = "300"
    _LLM_CANONICAL_NAMES = (
        "esprit_llm",
        "llm_api_key",
        "llm_api_base",
        "openai_api_base",
        "litellm_base_url",
        "ollama_api_base",
        "esprit_reasoning_effort",
        "esprit_llm_max_retries",
        "esprit_memory_compressor_timeout",
        "llm_timeout",
    )

    # Tool & Feature Configuration
    perplexity_api_key = None
    esprit_disable_browser = "false"

    # Runtime Configuration
    esprit_image = "improdead/esprit-sandbox:latest"
    esprit_docker_platform = None
    esprit_runtime_backend = "docker"
    esprit_sandbox_execution_timeout = "120"
    esprit_sandbox_connect_timeout = "10"

    # Telemetry
    esprit_telemetry = "1"

    # Config file override (set via --config CLI arg)
    _config_file_override: Path | None = None
    _UI_SECTION_KEY = "ui"
    _LAUNCHPAD_THEME_KEY = "launchpad_theme"
    _DEFAULT_LAUNCHPAD_THEME = "esprit"
    _ONBOARDING_SECTION_KEY = "onboarding"
    _ONBOARDING_STATE_KEY = "state"
    _ONBOARDING_VERSION_KEY = "version"
    _ONBOARDING_LAST_SEEN_AT_KEY = "last_seen_at"
    _ONBOARDING_COMPLETED_AT_KEY = "completed_at"
    _ONBOARDING_SKIP_COUNT_KEY = "skip_count"
    _ONBOARDING_STATE_PENDING = "pending"
    _ONBOARDING_STATE_SKIPPED = "skipped"
    _ONBOARDING_STATE_COMPLETED = "completed"
    _DEFAULT_ONBOARDING_VERSION = 1

    @classmethod
    def _tracked_names(cls) -> list[str]:
        return [
            k
            for k, v in vars(cls).items()
            if not k.startswith("_") and k[0].islower() and (v is None or isinstance(v, str))
        ]

    @classmethod
    def tracked_vars(cls) -> list[str]:
        return [name.upper() for name in cls._tracked_names()]

    @classmethod
    def _llm_env_vars(cls) -> set[str]:
        return {name.upper() for name in cls._LLM_CANONICAL_NAMES}

    @classmethod
    def _llm_env_changed(cls, saved_env: dict[str, Any]) -> bool:
        for var_name in cls._llm_env_vars():
            current = os.getenv(var_name)
            if current is None:
                continue
            if saved_env.get(var_name) != current:
                return True
        return False

    @classmethod
    def get(cls, name: str) -> str | None:
        env_name = name.upper()
        default = getattr(cls, name, None)
        return os.getenv(env_name, default)

    @classmethod
    def config_dir(cls) -> Path:
        return Path.home() / ".esprit"

    @classmethod
    def config_file(cls) -> Path:
        if cls._config_file_override is not None:
            return cls._config_file_override
        return cls.config_dir() / "cli-config.json"

    @classmethod
    def load(cls) -> dict[str, Any]:
        path = cls.config_file()
        if not path.exists():
            return {}
        try:
            with path.open("r", encoding="utf-8") as f:
                data: dict[str, Any] = json.load(f)
                return data
        except (json.JSONDecodeError, OSError):
            return {}

    @classmethod
    def save(cls, config: dict[str, Any]) -> bool:
        try:
            cls.config_dir().mkdir(parents=True, exist_ok=True)
            config_path = cls.config_dir() / "cli-config.json"
            with config_path.open("w", encoding="utf-8") as f:
                json.dump(config, f, indent=2)
        except OSError:
            return False
        with contextlib.suppress(OSError):
            config_path.chmod(0o600)  # may fail on Windows
        return True

    @classmethod
    def apply_saved(cls, force: bool = False) -> dict[str, str]:
        saved = cls.load()
        if not isinstance(saved, dict):
            saved = {}
        env_vars = saved.get("env", {})
        if not isinstance(env_vars, dict):
            env_vars = {}
        cleared_vars = {
            var_name
            for var_name in cls.tracked_vars()
            if var_name in os.environ and os.environ.get(var_name) == ""
        }
        if cleared_vars:
            for var_name in cleared_vars:
                env_vars.pop(var_name, None)
            if cls._config_file_override is None:
                saved["env"] = env_vars
                cls.save(saved)
        if cls._llm_env_changed(env_vars):
            for var_name in cls._llm_env_vars():
                env_vars.pop(var_name, None)
            if cls._config_file_override is None:
                saved["env"] = env_vars
                cls.save(saved)
        applied = {}

        for var_name, var_value in env_vars.items():
            if var_name in cls.tracked_vars() and (force or var_name not in os.environ):
                os.environ[var_name] = var_value
                applied[var_name] = var_value

        return applied

    @classmethod
    def capture_current(cls) -> dict[str, Any]:
        env_vars = {}
        for var_name in cls.tracked_vars():
            value = os.getenv(var_name)
            if value:
                env_vars[var_name] = value
        return {"env": env_vars}

    @classmethod
    def save_current(cls) -> bool:
        saved = cls.load()
        if not isinstance(saved, dict):
            saved = {}
        existing = saved.get("env", {})
        if not isinstance(existing, dict):
            existing = {}
        merged = dict(existing)

        for var_name in cls.tracked_vars():
            value = os.getenv(var_name)
            if value is None:
                pass
            elif value == "":
                merged.pop(var_name, None)
            else:
                merged[var_name] = value

        saved["env"] = merged
        return cls.save(saved)

    @classmethod
    def get_launchpad_theme(cls) -> str:
        saved = cls.load()
        if not isinstance(saved, dict):
            return cls._DEFAULT_LAUNCHPAD_THEME
        ui = saved.get(cls._UI_SECTION_KEY, {})
        if not isinstance(ui, dict):
            return cls._DEFAULT_LAUNCHPAD_THEME
        theme = ui.get(cls._LAUNCHPAD_THEME_KEY)
        if isinstance(theme, str) and theme:
            return theme
        return cls._DEFAULT_LAUNCHPAD_THEME

    @classmethod
    def save_launchpad_theme(cls, theme: str) -> bool:
        if not theme:
            return False
        saved = cls.load()
        if not isinstance(saved, dict):
            saved = {}
        ui = saved.get(cls._UI_SECTION_KEY, {})
        if not isinstance(ui, dict):
            ui = {}
        ui[cls._LAUNCHPAD_THEME_KEY] = theme
        saved[cls._UI_SECTION_KEY] = ui
        return cls.save(saved)

    @classmethod
    def _utc_now_iso(cls) -> str:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

    @classmethod
    def get_onboarding_state(cls) -> dict[str, Any]:
        saved = cls.load()
        if not isinstance(saved, dict):
            saved = {}
        ui = saved.get(cls._UI_SECTION_KEY, {})
        if not isinstance(ui, dict):
            ui = {}
        raw_state = ui.get(cls._ONBOARDING_SECTION_KEY, {})
        if not isinstance(raw_state, dict):
            raw_state = {}

        version = raw_state.get(cls._ONBOARDING_VERSION_KEY)
        if not isinstance(version, int):
            version = cls._DEFAULT_ONBOARDING_VERSION

        state = raw_state.get(cls._ONBOARDING_STATE_KEY)
        if not isinstance(state, str):
            state = cls._ONBOARDING_STATE_PENDING

        completed_at = raw_state.get(cls._ONBOARDING_COMPLETED_AT_KEY)
        if not isinstance(completed_at, str):
            completed_at = None

        last_seen_at = raw_state.get(cls._ONBOARDING_LAST_SEEN_AT_KEY)
        if not isinstance(last_seen_at, str):
            last_seen_at = None

        skip_count = raw_state.get(cls._ONBOARDING_SKIP_COUNT_KEY)
        if not isinstance(skip_count, int) or skip_count < 0:
            skip_count = 0

        return {
            cls._ONBOARDING_VERSION_KEY: version,
            cls._ONBOARDING_STATE_KEY: state,
            cls._ONBOARDING_COMPLETED_AT_KEY: completed_at,
            cls._ONBOARDING_LAST_SEEN_AT_KEY: last_seen_at,
            cls._ONBOARDING_SKIP_COUNT_KEY: skip_count,
        }

    @classmethod
    def _save_onboarding_state(cls, onboarding_state: dict[str, Any]) -> bool:
        saved = cls.load()
        if not isinstance(saved, dict):
            saved = {}
        ui = saved.get(cls._UI_SECTION_KEY, {})
        if not isinstance(ui, dict):
            ui = {}
        ui[cls._ONBOARDING_SECTION_KEY] = onboarding_state
        saved[cls._UI_SECTION_KEY] = ui
        return cls.save(saved)

    @classmethod
    def is_onboarding_required(cls, version: int | None = None) -> bool:
        target_version = (
            version
            if isinstance(version, int) and version > 0
            else cls._DEFAULT_ONBOARDING_VERSION
        )
        state = cls.get_onboarding_state()

        state_value = str(
            state.get(cls._ONBOARDING_STATE_KEY) or cls._ONBOARDING_STATE_PENDING
        ).lower()
        completed = state_value == cls._ONBOARDING_STATE_COMPLETED
        seen_version = state.get(cls._ONBOARDING_VERSION_KEY)
        if not isinstance(seen_version, int):
            seen_version = cls._DEFAULT_ONBOARDING_VERSION

        return not completed or seen_version < target_version

    @classmethod
    def mark_onboarding_completed(cls, version: int | None = None) -> bool:
        target_version = (
            version
            if isinstance(version, int) and version > 0
            else cls._DEFAULT_ONBOARDING_VERSION
        )
        now_iso = cls._utc_now_iso()
        existing = cls.get_onboarding_state()

        onboarding_state = {
            cls._ONBOARDING_VERSION_KEY: target_version,
            cls._ONBOARDING_STATE_KEY: cls._ONBOARDING_STATE_COMPLETED,
            cls._ONBOARDING_COMPLETED_AT_KEY: now_iso,
            cls._ONBOARDING_LAST_SEEN_AT_KEY: now_iso,
            cls._ONBOARDING_SKIP_COUNT_KEY: int(existing.get(cls._ONBOARDING_SKIP_COUNT_KEY, 0)),
        }
        return cls._save_onboarding_state(onboarding_state)

    @classmethod
    def mark_onboarding_skipped(cls, version: int | None = None) -> bool:
        target_version = (
            version
            if isinstance(version, int) and version > 0
            else cls._DEFAULT_ONBOARDING_VERSION
        )
        now_iso = cls._utc_now_iso()
        existing = cls.get_onboarding_state()
        skip_count = int(existing.get(cls._ONBOARDING_SKIP_COUNT_KEY, 0)) + 1

        onboarding_state = {
            cls._ONBOARDING_VERSION_KEY: target_version,
            cls._ONBOARDING_STATE_KEY: cls._ONBOARDING_STATE_SKIPPED,
            cls._ONBOARDING_COMPLETED_AT_KEY: existing.get(cls._ONBOARDING_COMPLETED_AT_KEY),
            cls._ONBOARDING_LAST_SEEN_AT_KEY: now_iso,
            cls._ONBOARDING_SKIP_COUNT_KEY: skip_count,
        }
        return cls._save_onboarding_state(onboarding_state)


def apply_saved_config(force: bool = False) -> dict[str, str]:
    return Config.apply_saved(force=force)


def save_current_config() -> bool:
    return Config.save_current()
