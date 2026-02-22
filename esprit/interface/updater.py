"""Self-update support for Esprit CLI.

Checks the GitHub Releases API for newer versions, caches the result for 24 h,
and can download + apply the update by re-running the official install script.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com/repos/improdead/Esprit/releases/latest"
_INSTALL_SCRIPT = "https://raw.githubusercontent.com/improdead/Esprit/main/scripts/install.sh"
_ESPRIT_DIR = Path.home() / ".esprit"
_CACHE_FILE = _ESPRIT_DIR / "update_check.json"
_PENDING_FILE = _ESPRIT_DIR / "pending_update"
_CACHE_TTL = 86_400  # 24 hours


@dataclass
class UpdateInfo:
    current: str
    latest: str
    release_url: str


def _current_version() -> str:
    """Return the running version string."""
    try:
        from importlib.metadata import version

        return version("esprit-cli")
    except Exception:
        try:
            from esprit._version import __version__

            return __version__
        except Exception:
            return "unknown"


def _vtuple(v: str) -> tuple[int, ...]:
    """Parse '0.7.1' → (0, 7, 1) for comparison."""
    try:
        return tuple(int(x) for x in v.strip().lstrip("v").split("."))
    except Exception:
        return (0,)


def check_for_update(force: bool = False) -> UpdateInfo | None:
    """Return UpdateInfo if a newer version is available on GitHub, else None.

    Results are cached for 24 h in ``~/.esprit/update_check.json`` so startup
    is not slowed down by a network round-trip on every launch.

    Never raises — network failures are logged at DEBUG and return None.
    """
    current = _current_version()

    if not force:
        try:
            if _CACHE_FILE.exists():
                cache = json.loads(_CACHE_FILE.read_text())
                age = time.time() - float(cache.get("checked_at", 0))
                if age < _CACHE_TTL:
                    latest = cache.get("latest_version", "")
                    if latest and _vtuple(latest) > _vtuple(current):
                        return UpdateInfo(current, latest, cache.get("release_url", ""))
                    return None  # Cache confirms we're up to date
        except Exception:
            pass  # Cache unreadable — fall through to network check

    try:
        import httpx

        resp = httpx.get(_GITHUB_API, timeout=5, follow_redirects=True)
        resp.raise_for_status()
        data = resp.json()
        tag = data.get("tag_name", "")
        latest = tag.lstrip("v")
        release_url = data.get("html_url", "")

        _ESPRIT_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(
            json.dumps(
                {
                    "checked_at": time.time(),
                    "latest_version": latest,
                    "release_url": release_url,
                }
            )
        )

        if latest and _vtuple(latest) > _vtuple(current):
            return UpdateInfo(current, latest, release_url)

    except Exception:
        logger.debug("Update check failed", exc_info=True)

    return None


def schedule_update() -> None:
    """Write a flag so the next Esprit launch auto-applies the update first."""
    _ESPRIT_DIR.mkdir(parents=True, exist_ok=True)
    _PENDING_FILE.write_text(str(time.time()))


def has_pending_update() -> bool:
    """Return True if a scheduled update is waiting to be applied."""
    return _PENDING_FILE.exists()


def clear_pending_update() -> None:
    """Remove the pending-update flag (called after a successful apply)."""
    try:
        _PENDING_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def apply_update(restart: bool = True) -> None:
    """Run the official install script to download and install the latest version.

    If *restart* is True and the install succeeds, this function never returns —
    it replaces the current process with the new binary via ``os.execv``.
    """
    clear_pending_update()

    result = subprocess.run(  # noqa: S602, S603
        f'curl -fsSL "{_INSTALL_SCRIPT}" | bash',
        shell=True,
    )

    if restart and result.returncode == 0:
        new_bin = _ESPRIT_DIR / "bin" / "esprit"
        target = str(new_bin) if new_bin.exists() else sys.argv[0]
        os.execv(target, [target, *sys.argv[1:]])  # noqa: S606
