"""Tests for hardened free-tier controls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.core.auth as auth_module
from app.api import routes
from app.main import app
from app.services.usage_service import UsageService


def _fake_user() -> auth_module.TokenPayload:
    return auth_module.TokenPayload(
        sub="user-free-1",
        email="free@esprit.dev",
        role="authenticated",
        exp=0,
    )


def test_claim_free_scan_duplicate_returns_denied(monkeypatch) -> None:
    usage = UsageService()

    class _InsertQuery:
        def execute(self):
            raise Exception("duplicate key value violates unique constraint")

    class _Table:
        def insert(self, _payload):
            return _InsertQuery()

    class _Supabase:
        def table(self, _name):
            return _Table()

    usage.supabase = _Supabase()  # type: ignore[assignment]

    claimed, message = asyncio.run(usage.claim_free_scan("user-free-1", "scan-1"))
    assert claimed is False
    assert message == "Free scan already used."


def test_free_quota_blocks_second_scan(monkeypatch) -> None:
    usage = UsageService()

    async def fake_get_usage(_user_id: str):
        return SimpleNamespace(
            scans_used=1,
            scans_limit=1,
            tokens_used=100,
            tokens_limit=1_000_000,
            plan="free",
        )

    monkeypatch.setattr(usage, "get_usage", fake_get_usage)

    quota = asyncio.run(usage.check_quota("user-free-1"))
    assert quota.has_quota is False
    assert quota.scans_remaining == 0


def test_subscription_verify_free_user_with_remaining_scan(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_get_user_plan(_user_id: str) -> str:
        return "free"

    async def fake_check_quota(_user_id: str, *args, **kwargs):
        return SimpleNamespace(
            has_quota=True,
            scans_remaining=1,
            tokens_remaining=900000,
            message=None,
        )

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/subscription/verify",
            headers={"Authorization": "Bearer ignored.for.override"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["valid"] is True
    assert payload["cloud_enabled"] is True
    assert payload["available_models"] == ["default", "haiku"]
    app.dependency_overrides.clear()


def test_subscription_verify_free_user_after_claim(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_get_user_plan(_user_id: str) -> str:
        return "free"

    async def fake_check_quota(_user_id: str, *args, **kwargs):
        return SimpleNamespace(
            has_quota=False,
            scans_remaining=0,
            tokens_remaining=500000,
            message="Your free Esprit cloud scan has already been used.",
        )

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/subscription/verify",
            headers={"Authorization": "Bearer ignored.for.override"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cloud_enabled"] is False
    assert payload["available_models"] == []
    app.dependency_overrides.clear()


def test_subscription_verify_paid_user_includes_full_model_set(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_get_user_plan(_user_id: str) -> str:
        return "pro"

    async def fake_check_quota(_user_id: str, *args, **kwargs):
        return SimpleNamespace(
            has_quota=True,
            scans_remaining=9,
            tokens_remaining=850000,
            message=None,
        )

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/subscription/verify",
            headers={"Authorization": "Bearer ignored.for.override"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["cloud_enabled"] is True
    assert payload["available_models"] == ["default", "haiku", "kimi-k2.5"]
    app.dependency_overrides.clear()


def test_start_scan_rejects_non_quick_for_free_plan(monkeypatch) -> None:
    scan_id = "00000000-0000-0000-0000-000000000123"

    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_get_user_plan(_user_id: str) -> str:
        return "free"

    class _ScanQuery:
        def select(self, *_args, **_kwargs):
            return self

        def eq(self, *_args, **_kwargs):
            return self

        def single(self):
            return self

        def execute(self):
            return SimpleNamespace(
                data={
                    "id": scan_id,
                    "user_id": "user-free-1",
                    "status": "pending",
                    "scan_type": "standard",
                    "target": "https://example.com",
                    "target_type": "url",
                }
            )

    class _Supabase:
        def table(self, _name):
            return _ScanQuery()

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)
    monkeypatch.setattr(routes, "supabase", _Supabase())

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/scans/{scan_id}/start",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={},
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "Free plan supports quick cloud scan mode only."
    app.dependency_overrides.clear()


def test_llm_generate_blocks_free_user_after_token_cap(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_get_user_plan(_user_id: str) -> str:
        return "free"

    async def fake_get_free_scan_claim(_user_id: str):
        return {"scan_id": "claim-1"}

    async def fake_check_quota(_user_id: str, *args, **kwargs):
        return SimpleNamespace(
            has_quota=False,
            scans_remaining=0,
            tokens_remaining=0,
            message="Your free scan reached the token limit.",
        )

    async def fake_generate(*args, **kwargs):
        raise AssertionError("llm_service.generate should not be called when quota is exhausted")

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)
    monkeypatch.setattr(routes.usage_service, "get_free_scan_claim", fake_get_free_scan_claim)
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)
    monkeypatch.setattr(routes.llm_service, "generate", fake_generate)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/llm/generate",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={"messages": [{"role": "user", "content": "hello"}]},
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "Your free scan reached the token limit."
    app.dependency_overrides.clear()
