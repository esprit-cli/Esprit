"""Regression tests for GitHub installation re-auth recovery."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

import app.core.auth as auth_module
from app.api import routes
from app.main import app


def _fake_user() -> auth_module.TokenPayload:
    return auth_module.TokenPayload(
        sub="user-github-1",
        email="github@esprit.dev",
        role="authenticated",
        exp=0,
    )


class _FakeSupabase:
    def __init__(self, installation_id: int | None) -> None:
        self.profile = {
            "github_app_installation_id": installation_id,
            "github_app_installed_at": "2026-02-01T00:00:00+00:00",
        }
        self.profile_clear_calls = 0
        self.linked_repo_clear_calls = 0

    def table(self, table_name: str):
        return _FakeQuery(self, table_name)


class _FakeQuery:
    def __init__(self, parent: _FakeSupabase, table_name: str) -> None:
        self.parent = parent
        self.table_name = table_name
        self.operation = "select"
        self.payload: dict | None = None
        self.filters: dict[str, object] = {}

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def update(self, payload: dict):
        self.operation = "update"
        self.payload = payload
        return self

    def delete(self):
        self.operation = "delete"
        return self

    def eq(self, field: str, value: object):
        self.filters[field] = value
        return self

    def single(self):
        return self

    def execute(self):
        if self.table_name == "profiles":
            if self.operation == "select":
                if self.filters.get("id") == "user-github-1":
                    return SimpleNamespace(data=dict(self.parent.profile))
                return SimpleNamespace(data=None)
            if self.operation == "update":
                self.parent.profile_clear_calls += 1
                self.parent.profile.update(self.payload or {})
                return SimpleNamespace(data=[dict(self.parent.profile)])

        if self.table_name == "linked_repos" and self.operation == "delete":
            self.parent.linked_repo_clear_calls += 1
            return SimpleNamespace(data=[])

        return SimpleNamespace(data=[])


def test_github_status_clears_stale_installation(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def stale_token(*_args, **_kwargs):
        raise HTTPException(status_code=401, detail="GitHub App installation is no longer valid. Please reconnect GitHub.")

    fake_supabase = _FakeSupabase(installation_id=12345)
    monkeypatch.setattr(routes, "supabase", fake_supabase)
    monkeypatch.setattr(routes, "get_github_app_installation_token", stale_token)
    app.dependency_overrides[auth_module.get_current_user] = fake_user

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/github/app/status",
            headers={"Authorization": "Bearer ignored.for.override"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["installed"] is False
    assert fake_supabase.profile_clear_calls == 1
    assert fake_supabase.linked_repo_clear_calls == 1
    app.dependency_overrides.clear()


def test_github_repos_returns_reconnect_when_installation_stale(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def stale_token(*_args, **_kwargs):
        raise HTTPException(status_code=401, detail="GitHub App installation is no longer valid. Please reconnect GitHub.")

    fake_supabase = _FakeSupabase(installation_id=12345)
    monkeypatch.setattr(routes, "supabase", fake_supabase)
    monkeypatch.setattr(routes, "get_github_app_installation_token", stale_token)
    app.dependency_overrides[auth_module.get_current_user] = fake_user

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/github/repos",
            headers={"Authorization": "Bearer ignored.for.override"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "GitHub connection expired. Please reconnect GitHub."
    assert fake_supabase.profile_clear_calls == 1
    assert fake_supabase.linked_repo_clear_calls == 1
    app.dependency_overrides.clear()


def test_github_repos_does_not_clear_on_non_stale_error(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def transient_error(*_args, **_kwargs):
        raise HTTPException(status_code=502, detail="Failed to get installation token")

    fake_supabase = _FakeSupabase(installation_id=12345)
    monkeypatch.setattr(routes, "supabase", fake_supabase)
    monkeypatch.setattr(routes, "get_github_app_installation_token", transient_error)
    app.dependency_overrides[auth_module.get_current_user] = fake_user

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/github/repos",
            headers={"Authorization": "Bearer ignored.for.override"},
        )

    assert response.status_code == 502
    assert fake_supabase.profile_clear_calls == 0
    assert fake_supabase.linked_repo_clear_calls == 0
    app.dependency_overrides.clear()
