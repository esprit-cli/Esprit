"""Tests for auth validation and quota bypass controls."""

from __future__ import annotations

import time

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from jose import jwt

import app.core.auth as auth_module
from app.services.usage_service import UsageService


@pytest.mark.asyncio
async def test_get_current_user_accepts_esprit_cli_token_with_legacy_secret_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module.settings, "auth_jwt_secret", "")
    monkeypatch.setattr(auth_module.settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(
        auth_module.settings,
        "supabase_service_key",
        "legacy-signing-secret-1234567890abcdef.extra",
    )
    signing_secret = auth_module.settings.supabase_service_key[:32]
    token = jwt.encode(
        {
            "sub": "user-1",
            "email": "demo@esprit.dev",
            "role": "authenticated",
            "iss": "esprit-cli",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        key=signing_secret,
        algorithm="HS256",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = await auth_module.get_current_user(creds)
    assert user.sub == "user-1"
    assert user.email == "demo@esprit.dev"


@pytest.mark.asyncio
async def test_get_current_user_rejects_invalid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module.settings, "auth_jwt_secret", "")
    monkeypatch.setattr(auth_module.settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(
        auth_module.settings,
        "supabase_service_key",
        "legacy-signing-secret-1234567890abcdef.extra",
    )
    token = jwt.encode(
        {
            "sub": "user-1",
            "iss": "esprit-cli",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        key="wrong-signing-secret",
        algorithm="HS256",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as excinfo:
        await auth_module.get_current_user(creds)
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_rejects_expired_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module.settings, "auth_jwt_secret", "")
    monkeypatch.setattr(auth_module.settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(
        auth_module.settings,
        "supabase_service_key",
        "legacy-signing-secret-1234567890abcdef.extra",
    )
    signing_secret = auth_module.settings.supabase_service_key[:32]
    token = jwt.encode(
        {
            "sub": "user-1",
            "iss": "esprit-cli",
            "aud": "authenticated",
            "exp": int(time.time()) - 10,
        },
        key=signing_secret,
        algorithm="HS256",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as excinfo:
        await auth_module.get_current_user(creds)
    assert excinfo.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_accepts_supabase_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module.settings, "auth_jwt_secret", "supabase-test-secret")
    monkeypatch.setattr(auth_module.settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(auth_module.settings, "supabase_service_key", "")
    monkeypatch.setattr(auth_module.settings, "supabase_url", "https://abcxyz.supabase.co")
    token = jwt.encode(
        {
            "sub": "user-2",
            "email": "web@esprit.dev",
            "role": "authenticated",
            "iss": "https://abcxyz.supabase.co/auth/v1",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        key="supabase-test-secret",
        algorithm="HS256",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = await auth_module.get_current_user(creds)
    assert user.sub == "user-2"
    assert user.email == "web@esprit.dev"


@pytest.mark.asyncio
async def test_get_current_user_rejects_unknown_issuer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth_module.settings, "auth_jwt_secret", "supabase-test-secret")
    monkeypatch.setattr(auth_module.settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(auth_module.settings, "supabase_service_key", "")
    monkeypatch.setattr(auth_module.settings, "supabase_url", "https://abcxyz.supabase.co")
    token = jwt.encode(
        {
            "sub": "user-2",
            "email": "web@esprit.dev",
            "role": "authenticated",
            "iss": "https://malicious.example/auth/v1",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        key="supabase-test-secret",
        algorithm="HS256",
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as excinfo:
        await auth_module.get_current_user(creds)
    assert excinfo.value.status_code == 401
    assert excinfo.value.detail == "Authentication token issuer is invalid."


def test_quota_bypass_disabled_by_default() -> None:
    usage = UsageService()
    assert usage._is_quota_bypass_allowed("ESPRIT-DEMO-2024") is False


@pytest.mark.asyncio
async def test_get_current_user_accepts_token_via_supabase_userinfo_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module.settings, "auth_jwt_secret", "")
    monkeypatch.setattr(auth_module.settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(auth_module.settings, "supabase_service_key", "service-key-value")
    monkeypatch.setattr(auth_module.settings, "supabase_url", "https://abcxyz.supabase.co")
    auth_module._token_cache.clear()

    token = jwt.encode(
        {
            "sub": "user-web-1",
            "email": "web-fallback@esprit.dev",
            "role": "authenticated",
            "iss": "https://abcxyz.supabase.co/auth/v1",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        key="not-a-configured-secret",
        algorithm="HS256",
    )

    class _Response:
        status_code = 200

        @staticmethod
        def json() -> dict:
            return {"id": "user-web-1", "email": "web-fallback@esprit.dev"}

    def _fake_get(url: str, headers: dict, timeout: int):
        assert url == "https://abcxyz.supabase.co/auth/v1/user"
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["apikey"] == "service-key-value"
        assert timeout == 5
        return _Response()

    monkeypatch.setattr(auth_module.requests, "get", _fake_get)

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    user = await auth_module.get_current_user(creds)
    assert user.sub == "user-web-1"
    assert user.email == "web-fallback@esprit.dev"


@pytest.mark.asyncio
async def test_get_current_user_rejects_token_when_supabase_fallback_rejects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_module.settings, "auth_jwt_secret", "")
    monkeypatch.setattr(auth_module.settings, "supabase_jwt_secret", "")
    monkeypatch.setattr(auth_module.settings, "supabase_service_key", "service-key-value")
    monkeypatch.setattr(auth_module.settings, "supabase_url", "https://abcxyz.supabase.co")
    auth_module._token_cache.clear()

    token = jwt.encode(
        {
            "sub": "user-web-2",
            "iss": "https://abcxyz.supabase.co/auth/v1",
            "aud": "authenticated",
            "exp": int(time.time()) + 3600,
        },
        key="not-a-configured-secret",
        algorithm="HS256",
    )

    class _Response:
        status_code = 401

        @staticmethod
        def json() -> dict:
            return {}

    monkeypatch.setattr(auth_module.requests, "get", lambda *args, **kwargs: _Response())

    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    with pytest.raises(HTTPException) as excinfo:
        await auth_module.get_current_user(creds)
    assert excinfo.value.status_code == 401
