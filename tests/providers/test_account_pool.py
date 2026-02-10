"""Tests for the multi-account credential pool."""

import json
import os
import time
from pathlib import Path

import pytest

from esprit.providers.account_pool import (
    BACKOFF_RESET_S,
    BACKOFF_TIERS_S,
    AccountEntry,
    AccountPool,
)
from esprit.providers.base import OAuthCredentials


@pytest.fixture
def tmp_pool(tmp_path: Path) -> AccountPool:
    """Create an AccountPool backed by a temporary directory."""
    return AccountPool(config_dir=tmp_path)


def _make_creds(
    email: str = "user@example.com",
    access: str = "tok_abc",
    refresh: str = "ref_abc",
    expires_ms: int | None = None,
) -> OAuthCredentials:
    return OAuthCredentials(
        type="oauth",
        access_token=access,
        refresh_token=refresh,
        expires_at=expires_ms or int((time.time() + 3600) * 1000),
        extra={"email": email},
    )


# ── Basic operations ───────────────────────────────────────────


class TestAccountPoolBasics:
    def test_empty_pool_has_no_accounts(self, tmp_pool: AccountPool) -> None:
        assert tmp_pool.has_accounts("openai") is False
        assert tmp_pool.account_count("openai") == 0
        assert tmp_pool.list_accounts("openai") == []

    def test_add_and_list_account(self, tmp_pool: AccountPool) -> None:
        creds = _make_creds("alice@test.com")
        tmp_pool.add_account("openai", creds, "alice@test.com")
        assert tmp_pool.has_accounts("openai") is True
        assert tmp_pool.account_count("openai") == 1
        accounts = tmp_pool.list_accounts("openai")
        assert len(accounts) == 1
        assert accounts[0].email == "alice@test.com"
        assert accounts[0].credentials.access_token == "tok_abc"

    def test_add_duplicate_replaces(self, tmp_pool: AccountPool) -> None:
        creds1 = _make_creds("alice@test.com", access="tok_v1")
        creds2 = _make_creds("alice@test.com", access="tok_v2")
        tmp_pool.add_account("openai", creds1, "alice@test.com")
        tmp_pool.add_account("openai", creds2, "alice@test.com")
        assert tmp_pool.account_count("openai") == 1
        assert tmp_pool.list_accounts("openai")[0].credentials.access_token == "tok_v2"

    def test_remove_account(self, tmp_pool: AccountPool) -> None:
        creds = _make_creds("bob@test.com")
        tmp_pool.add_account("openai", creds, "bob@test.com")
        assert tmp_pool.remove_account("openai", "bob@test.com") is True
        assert tmp_pool.account_count("openai") == 0
        # Removing again should return False
        assert tmp_pool.remove_account("openai", "bob@test.com") is False

    def test_update_credentials(self, tmp_pool: AccountPool) -> None:
        creds = _make_creds("carol@test.com", access="old_tok")
        tmp_pool.add_account("openai", creds, "carol@test.com")
        new_creds = _make_creds("carol@test.com", access="new_tok")
        tmp_pool.update_credentials("openai", "carol@test.com", new_creds)
        accounts = tmp_pool.list_accounts("openai")
        assert accounts[0].credentials.access_token == "new_tok"


# ── Serialization round-trip ───────────────────────────────────


class TestSerialization:
    def test_round_trip(self, tmp_pool: AccountPool) -> None:
        creds = OAuthCredentials(
            type="oauth",
            access_token="at",
            refresh_token="rt",
            expires_at=9999999999999,
            account_id="acct_123",
            enterprise_url="https://ent.example.com",
            extra={"email": "dave@test.com", "tier": "plus"},
        )
        tmp_pool.add_account("openai", creds, "dave@test.com")

        # Create a new pool to force re-read from disk
        pool2 = AccountPool(config_dir=tmp_pool.config_dir)
        accounts = pool2.list_accounts("openai")
        assert len(accounts) == 1
        acct = accounts[0]
        assert acct.email == "dave@test.com"
        assert acct.credentials.access_token == "at"
        assert acct.credentials.refresh_token == "rt"
        assert acct.credentials.account_id == "acct_123"
        assert acct.credentials.enterprise_url == "https://ent.example.com"
        assert acct.credentials.extra["tier"] == "plus"

    def test_api_key_round_trip(self, tmp_pool: AccountPool) -> None:
        creds = OAuthCredentials(type="api", access_token="sk-12345")
        tmp_pool.add_account("openai", creds, "apikey@test.com")
        pool2 = AccountPool(config_dir=tmp_pool.config_dir)
        accounts = pool2.list_accounts("openai")
        assert accounts[0].credentials.type == "api"
        assert accounts[0].credentials.access_token == "sk-12345"


# ── Atomic writes ──────────────────────────────────────────────


class TestAtomicWrites:
    def test_accounts_file_permissions(self, tmp_pool: AccountPool) -> None:
        if os.name == "nt":
            pytest.skip("Unix permissions only")
        creds = _make_creds("perm@test.com")
        tmp_pool.add_account("openai", creds, "perm@test.com")
        stat = os.stat(tmp_pool.accounts_file)
        assert (stat.st_mode & 0o777) == 0o600

    def test_corrupt_file_recovers(self, tmp_pool: AccountPool) -> None:
        # Write garbage to accounts.json
        tmp_pool.config_dir.mkdir(parents=True, exist_ok=True)
        tmp_pool.accounts_file.write_text("{{not json}", encoding="utf-8")
        # Force reload
        tmp_pool._pools = None
        assert tmp_pool.list_accounts("openai") == []

    def test_no_temp_files_left_on_success(self, tmp_pool: AccountPool) -> None:
        creds = _make_creds("clean@test.com")
        tmp_pool.add_account("openai", creds, "clean@test.com")
        temp_files = list(tmp_pool.config_dir.glob("accounts_*.tmp"))
        assert temp_files == []


# ── Account selection strategies ───────────────────────────────


class TestAccountSelection:
    def test_peek_does_not_save(self, tmp_pool: AccountPool) -> None:
        creds = _make_creds("peek@test.com")
        tmp_pool.add_account("openai", creds, "peek@test.com")

        # Read the file mtime
        mtime_before = tmp_pool.accounts_file.stat().st_mtime_ns

        # peek should not write
        result = tmp_pool.peek_best_account("openai")
        assert result is not None
        assert result.email == "peek@test.com"

        # File should not have been modified
        mtime_after = tmp_pool.accounts_file.stat().st_mtime_ns
        assert mtime_before == mtime_after

    def test_get_best_account_saves(self, tmp_pool: AccountPool) -> None:
        creds = _make_creds("save@test.com")
        tmp_pool.add_account("openai", creds, "save@test.com")
        mtime_before = tmp_pool.accounts_file.stat().st_mtime_ns

        # Small sleep to ensure mtime changes
        time.sleep(0.01)

        result = tmp_pool.get_best_account("openai")
        assert result is not None
        mtime_after = tmp_pool.accounts_file.stat().st_mtime_ns
        assert mtime_after > mtime_before

    def test_sticky_prefers_current(self, tmp_pool: AccountPool) -> None:
        tmp_pool.add_account("openai", _make_creds("a@t.com", access="tok_a"), "a@t.com")
        tmp_pool.add_account("openai", _make_creds("b@t.com", access="tok_b"), "b@t.com")

        acct = tmp_pool.get_best_account("openai")
        # Should consistently return the same account (active_index=0)
        acct2 = tmp_pool.get_best_account("openai")
        assert acct.email == acct2.email

    def test_rotate_picks_next(self, tmp_pool: AccountPool) -> None:
        tmp_pool.add_account("openai", _make_creds("a@t.com"), "a@t.com")
        tmp_pool.add_account("openai", _make_creds("b@t.com"), "b@t.com")

        first = tmp_pool.get_best_account("openai")
        rotated = tmp_pool.rotate("openai")
        assert rotated is not None
        assert rotated.email != first.email

    def test_rotate_single_account_returns_none(self, tmp_pool: AccountPool) -> None:
        tmp_pool.add_account("openai", _make_creds("solo@t.com"), "solo@t.com")
        assert tmp_pool.rotate("openai") is None


# ── Rate limiting ──────────────────────────────────────────────


class TestRateLimiting:
    def test_mark_rate_limited(self, tmp_pool: AccountPool) -> None:
        tmp_pool.add_account("openai", _make_creds("rl@t.com"), "rl@t.com")
        tmp_pool.mark_rate_limited("openai", "rl@t.com", "gpt-5", 60.0)

        accounts = tmp_pool.list_accounts("openai")
        assert "gpt-5" in accounts[0].rate_limits
        assert accounts[0].consecutive_429s == 1
        assert accounts[0].cooling_until is not None

    def test_escalating_backoff(self, tmp_pool: AccountPool) -> None:
        tmp_pool.add_account("openai", _make_creds("esc@t.com"), "esc@t.com")

        # First 429
        tmp_pool.mark_rate_limited("openai", "esc@t.com", "gpt-5", 60.0)
        accounts = tmp_pool.list_accounts("openai")
        assert accounts[0].consecutive_429s == 1

        # Second 429 within BACKOFF_RESET_S
        tmp_pool.mark_rate_limited("openai", "esc@t.com", "gpt-5", 60.0)
        accounts = tmp_pool.list_accounts("openai")
        assert accounts[0].consecutive_429s == 2

    def test_rate_limit_skips_affected_account(self, tmp_pool: AccountPool) -> None:
        tmp_pool.add_account("openai", _make_creds("a@t.com"), "a@t.com")
        tmp_pool.add_account("openai", _make_creds("b@t.com"), "b@t.com")

        # Rate-limit account a for gpt-5
        tmp_pool.mark_rate_limited("openai", "a@t.com", "gpt-5", 3600.0)

        # Requesting gpt-5 should prefer b
        acct = tmp_pool.get_best_account("openai", model="gpt-5")
        assert acct is not None
        assert acct.email == "b@t.com"
