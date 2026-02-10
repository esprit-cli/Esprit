"""
Multi-account credential pool with rate-limit-aware rotation.

Stores multiple OAuth accounts per provider in ~/.esprit/accounts.json.
Supports sticky (stay until rate-limited) and round-robin strategies.
"""

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from esprit.providers.base import OAuthCredentials

logger = logging.getLogger(__name__)

# Escalating backoff for consecutive 429s on the same account
BACKOFF_TIERS_S = [60, 300, 1800, 7200]  # 1m, 5m, 30m, 2h
BACKOFF_RESET_S = 120  # reset counter after 2min of no 429s


@dataclass
class AccountEntry:
    """A single account within a provider pool."""

    email: str
    credentials: OAuthCredentials
    account_id: str | None = None
    enabled: bool = True
    added_at: int = 0  # ms
    last_used: int | None = None  # ms
    rate_limits: dict[str, int] = field(default_factory=dict)  # model -> reset_at (ms)
    cooling_until: int | None = None  # ms
    consecutive_429s: int = 0
    last_429_at: int | None = None  # ms


class AccountPool:
    """Multi-account credential pool with rotation strategies."""

    def __init__(self, config_dir: Path | None = None):
        self.config_dir = config_dir or Path.home() / ".esprit"
        self.accounts_file = self.config_dir / "accounts.json"
        self._pools: dict[str, dict[str, Any]] | None = None

    # ── persistence ──────────────────────────────────────────────

    def _load(self) -> dict[str, dict[str, Any]]:
        if self._pools is not None:
            return self._pools
        if not self.accounts_file.exists():
            self._pools = {}
            return self._pools
        try:
            with self.accounts_file.open(encoding="utf-8") as f:
                data = json.load(f)
            self._pools = data.get("pools", {})
        except (json.JSONDecodeError, OSError):
            self._pools = {}
        return self._pools

    def _save(self) -> None:
        self.config_dir.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "pools": self._load()}
        with self.accounts_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        if os.name != "nt":
            os.chmod(self.accounts_file, 0o600)

    def _get_pool(self, provider_id: str) -> dict[str, Any]:
        pools = self._load()
        if provider_id not in pools:
            pools[provider_id] = {
                "accounts": [],
                "active_index": 0,
                "strategy": "sticky",
            }
        return pools[provider_id]

    # ── serialization helpers ────────────────────────────────────

    @staticmethod
    def _account_to_dict(acct: AccountEntry) -> dict[str, Any]:
        creds = acct.credentials
        creds_data: dict[str, Any] = {"type": creds.type}
        if creds.type == "oauth":
            creds_data.update({
                "access": creds.access_token,
                "refresh": creds.refresh_token,
                "expires": creds.expires_at,
            })
            if creds.account_id:
                creds_data["accountId"] = creds.account_id
            if creds.enterprise_url:
                creds_data["enterpriseUrl"] = creds.enterprise_url
            if creds.extra:
                creds_data["extra"] = creds.extra
        elif creds.type == "api":
            creds_data["key"] = creds.access_token
        return {
            "email": acct.email,
            "credentials": creds_data,
            "account_id": acct.account_id,
            "enabled": acct.enabled,
            "added_at": acct.added_at,
            "last_used": acct.last_used,
            "rate_limits": acct.rate_limits,
            "cooling_until": acct.cooling_until,
            "consecutive_429s": acct.consecutive_429s,
            "last_429_at": acct.last_429_at,
        }

    @staticmethod
    def _dict_to_account(d: dict[str, Any]) -> AccountEntry:
        cd = d.get("credentials", {})
        cred_type = cd.get("type", "oauth")
        if cred_type == "oauth":
            creds = OAuthCredentials(
                type="oauth",
                access_token=cd.get("access"),
                refresh_token=cd.get("refresh"),
                expires_at=cd.get("expires"),
                account_id=cd.get("accountId"),
                enterprise_url=cd.get("enterpriseUrl"),
                extra=cd.get("extra", {}),
            )
        elif cred_type == "api":
            creds = OAuthCredentials(type="api", access_token=cd.get("key"))
        else:
            creds = OAuthCredentials(type=cred_type)
        return AccountEntry(
            email=d.get("email", "unknown"),
            credentials=creds,
            account_id=d.get("account_id"),
            enabled=d.get("enabled", True),
            added_at=d.get("added_at", 0),
            last_used=d.get("last_used"),
            rate_limits=d.get("rate_limits", {}),
            cooling_until=d.get("cooling_until"),
            consecutive_429s=d.get("consecutive_429s", 0),
            last_429_at=d.get("last_429_at"),
        )

    def _load_accounts(self, provider_id: str) -> list[AccountEntry]:
        pool = self._get_pool(provider_id)
        return [self._dict_to_account(a) for a in pool.get("accounts", [])]

    def _save_accounts(self, provider_id: str, accounts: list[AccountEntry]) -> None:
        pool = self._get_pool(provider_id)
        pool["accounts"] = [self._account_to_dict(a) for a in accounts]
        self._save()

    # ── public API ───────────────────────────────────────────────

    def has_accounts(self, provider_id: str) -> bool:
        accounts = self._load_accounts(provider_id)
        return any(a.enabled for a in accounts)

    def add_account(
        self,
        provider_id: str,
        credentials: OAuthCredentials,
        email: str,
    ) -> None:
        accounts = self._load_accounts(provider_id)
        # Replace existing account with same email
        accounts = [a for a in accounts if a.email != email]
        accounts.append(
            AccountEntry(
                email=email,
                credentials=credentials,
                account_id=credentials.account_id,
                added_at=int(time.time() * 1000),
            )
        )
        self._save_accounts(provider_id, accounts)

    def remove_account(self, provider_id: str, email: str) -> bool:
        accounts = self._load_accounts(provider_id)
        before = len(accounts)
        accounts = [a for a in accounts if a.email != email]
        if len(accounts) == before:
            return False
        self._save_accounts(provider_id, accounts)
        return True

    def list_accounts(self, provider_id: str) -> list[AccountEntry]:
        return self._load_accounts(provider_id)

    def account_count(self, provider_id: str) -> int:
        return len([a for a in self._load_accounts(provider_id) if a.enabled])

    def get_active_credentials(self, provider_id: str) -> OAuthCredentials | None:
        """Get credentials for the current active account."""
        acct = self.get_best_account(provider_id)
        return acct.credentials if acct else None

    def get_best_account(
        self, provider_id: str, model: str | None = None
    ) -> AccountEntry | None:
        """Get the best available account, respecting rate limits."""
        accounts = self._load_accounts(provider_id)
        if not accounts:
            return None

        now_ms = int(time.time() * 1000)
        self._clear_expired_limits(accounts, now_ms)

        pool = self._get_pool(provider_id)
        strategy = pool.get("strategy", "sticky")
        active_idx = pool.get("active_index", 0)

        # Filter to enabled accounts not currently cooling
        available = [
            (i, a)
            for i, a in enumerate(accounts)
            if a.enabled and (not a.cooling_until or a.cooling_until <= now_ms)
        ]
        if not available:
            # All cooling — return first enabled anyway
            available = [(i, a) for i, a in enumerate(accounts) if a.enabled]
            if not available:
                return None

        # Filter out rate-limited accounts for this model
        if model:
            not_limited = [
                (i, a) for i, a in available if model not in a.rate_limits
            ]
            if not_limited:
                available = not_limited
            # else: all are limited for this model, return best anyway

        if strategy == "round-robin":
            # Pick the next one after active_index
            available.sort(key=lambda x: (x[0] <= active_idx, x[0]))
            chosen_idx, chosen = available[0]
        else:
            # Sticky: prefer current active if available
            active_match = [
                (i, a) for i, a in available if i == active_idx
            ]
            if active_match:
                chosen_idx, chosen = active_match[0]
            else:
                chosen_idx, chosen = available[0]

        # Update state
        chosen.last_used = now_ms
        pool["active_index"] = chosen_idx
        self._save_accounts(provider_id, accounts)
        return chosen

    def mark_rate_limited(
        self,
        provider_id: str,
        email: str,
        model: str,
        reset_seconds: float,
    ) -> None:
        """Mark an account as rate-limited for a specific model."""
        accounts = self._load_accounts(provider_id)
        now_ms = int(time.time() * 1000)

        for acct in accounts:
            if acct.email == email:
                reset_at = now_ms + int(reset_seconds * 1000)
                acct.rate_limits[model] = reset_at

                # Escalating backoff for consecutive 429s
                if (
                    acct.last_429_at
                    and (now_ms - acct.last_429_at) < BACKOFF_RESET_S * 1000
                ):
                    acct.consecutive_429s += 1
                else:
                    acct.consecutive_429s = 1
                acct.last_429_at = now_ms

                tier = min(acct.consecutive_429s - 1, len(BACKOFF_TIERS_S) - 1)
                cooldown = BACKOFF_TIERS_S[tier]
                acct.cooling_until = now_ms + cooldown * 1000

                logger.info(
                    "Account %s rate-limited for %s (429 #%d, cooldown %ds)",
                    email,
                    model,
                    acct.consecutive_429s,
                    cooldown,
                )
                break

        self._save_accounts(provider_id, accounts)

    def rotate(
        self, provider_id: str, model: str | None = None
    ) -> AccountEntry | None:
        """Force rotation to the next available account."""
        pool = self._get_pool(provider_id)
        accounts = self._load_accounts(provider_id)
        if len(accounts) <= 1:
            return None

        current = pool.get("active_index", 0)
        now_ms = int(time.time() * 1000)
        self._clear_expired_limits(accounts, now_ms)

        # Find next available account that's different from current
        for offset in range(1, len(accounts)):
            idx = (current + offset) % len(accounts)
            acct = accounts[idx]
            if not acct.enabled:
                continue
            if acct.cooling_until and acct.cooling_until > now_ms:
                continue
            if model and model in acct.rate_limits:
                continue
            pool["active_index"] = idx
            acct.last_used = now_ms
            self._save_accounts(provider_id, accounts)
            logger.info("Rotated to account %s for %s", acct.email, provider_id)
            return acct

        return None

    def update_credentials(
        self,
        provider_id: str,
        email: str,
        credentials: OAuthCredentials,
    ) -> None:
        """Update credentials for an existing account (e.g., after token refresh)."""
        accounts = self._load_accounts(provider_id)
        for acct in accounts:
            if acct.email == email:
                acct.credentials = credentials
                break
        self._save_accounts(provider_id, accounts)

    # ── private ──────────────────────────────────────────────────

    @staticmethod
    def _clear_expired_limits(
        accounts: list[AccountEntry], now_ms: int
    ) -> None:
        for acct in accounts:
            expired = [m for m, t in acct.rate_limits.items() if t <= now_ms]
            for m in expired:
                del acct.rate_limits[m]
            if acct.cooling_until and acct.cooling_until <= now_ms:
                acct.cooling_until = None
            # Reset consecutive counter if no recent 429s
            if (
                acct.last_429_at
                and (now_ms - acct.last_429_at) > BACKOFF_RESET_S * 1000
            ):
                acct.consecutive_429s = 0


# ── singleton ────────────────────────────────────────────────────

_pool: AccountPool | None = None


def get_account_pool() -> AccountPool:
    global _pool
    if _pool is None:
        _pool = AccountPool()
    return _pool
