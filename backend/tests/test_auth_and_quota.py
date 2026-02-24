"""Tests for auth validation and quota bypass controls."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

import app.core.auth as auth_module
from app.services.usage_service import UsageService


@pytest.mark.asyncio
async def test_get_current_user_validates_with_supabase(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAuth:
        def get_user(self, jwt: str | None = None) -> SimpleNamespace:
            return SimpleNamespace(user=SimpleNamespace(id="user-1", email="demo@esprit.dev"))

    monkeypatch.setattr(auth_module, "supabase", SimpleNamespace(auth=FakeAuth()))

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="valid.jwt.token")
    user = await auth_module.get_current_user(creds)
    assert user.sub == "user-1"
    assert user.email == "demo@esprit.dev"


@pytest.mark.asyncio
async def test_get_current_user_rejects_invalid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    class FailingAuth:
        def get_user(self, jwt: str | None = None) -> SimpleNamespace:
            raise RuntimeError("invalid token")

    monkeypatch.setattr(auth_module, "supabase", SimpleNamespace(auth=FailingAuth()))

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid.jwt.token")
    with pytest.raises(HTTPException) as excinfo:
        await auth_module.get_current_user(creds)
    assert excinfo.value.status_code == 401


def test_quota_bypass_disabled_by_default() -> None:
    usage = UsageService()
    assert usage._is_quota_bypass_allowed("ESPRIT-DEMO-2024") is False
