"""Tests for sandbox API route hardening."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.core.auth as auth_module
from app.api import routes
from app.main import app


def _fake_user() -> auth_module.TokenPayload:
    return auth_module.TokenPayload(
        sub="user-sandbox-1",
        email="sandbox@esprit.dev",
        role="authenticated",
        exp=0,
    )


class _ScansQuery:
    def __init__(self, rows: dict[str, dict]) -> None:
        self.rows = rows
        self.eq_id: str | None = None
        self.insert_payload: dict | None = None
        self.operation = "select"

    def select(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        self.operation = "select"
        return self

    def eq(self, field: str, value: str):  # type: ignore[no-untyped-def]
        if field == "id":
            self.eq_id = value
        return self

    def limit(self, _value: int):  # type: ignore[no-untyped-def]
        return self

    def insert(self, payload: dict):  # type: ignore[no-untyped-def]
        self.operation = "insert"
        self.insert_payload = payload
        return self

    def execute(self):  # type: ignore[no-untyped-def]
        if self.operation == "insert":
            assert self.insert_payload is not None
            row_id = str(self.insert_payload.get("id"))
            self.rows[row_id] = dict(self.insert_payload)
            return SimpleNamespace(data=[dict(self.insert_payload)])

        if self.eq_id and self.eq_id in self.rows:
            return SimpleNamespace(data=[dict(self.rows[self.eq_id])])
        return SimpleNamespace(data=[])


class _FakeSupabase:
    def __init__(self) -> None:
        self.scan_rows: dict[str, dict] = {}

    def table(self, _name: str):  # type: ignore[no-untyped-def]
        return _ScansQuery(self.scan_rows)


def test_sandbox_create_free_rejects_non_quick(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_check_quota(*_args, **_kwargs):
        return SimpleNamespace(has_quota=True, scans_remaining=1, tokens_remaining=0, message=None)

    async def fake_get_user_plan(_user_id: str) -> str:
        return "free"

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes, "supabase", _FakeSupabase())
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sandbox",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={
                "scan_id": "scan-123",
                "target": "https://example.com",
                "target_type": "url",
                "scan_type": "deep",
            },
        )

    assert response.status_code == 402
    assert response.json()["detail"] == "Free plan supports quick cloud scan mode only."
    app.dependency_overrides.clear()


def test_sandbox_create_free_claim_enforced(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_check_quota(*_args, **_kwargs):
        return SimpleNamespace(has_quota=True, scans_remaining=1, tokens_remaining=0, message=None)

    async def fake_get_user_plan(_user_id: str) -> str:
        return "free"

    claims = {"count": 0}

    async def fake_claim_free_scan(*_args, **_kwargs):
        claims["count"] += 1
        if claims["count"] == 1:
            return True, None
        return False, "Free scan already used."

    async def fake_create_sandbox(_request, _user_id):
        return {
            "sandbox_id": "sandbox-1",
            "status": "creating",
            "tool_server_url": None,
            "expires_at": None,
        }

    async def fake_increment_scan_count(*_args, **_kwargs):
        return None

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes, "supabase", _FakeSupabase())
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)
    monkeypatch.setattr(routes.usage_service, "claim_free_scan", fake_claim_free_scan)
    monkeypatch.setattr(routes.sandbox_service, "create_sandbox", fake_create_sandbox)
    monkeypatch.setattr(routes.usage_service, "increment_scan_count", fake_increment_scan_count)

    with TestClient(app) as client:
        payload = {
            "scan_id": "scan-quick-1",
            "target": "https://example.com",
            "target_type": "url",
            "scan_type": "quick",
        }
        first = client.post(
            "/api/v1/sandbox",
            headers={"Authorization": "Bearer ignored.for.override"},
            json=payload,
        )
        second = client.post(
            "/api/v1/sandbox",
            headers={"Authorization": "Bearer ignored.for.override"},
            json=payload,
        )

    assert first.status_code == 200
    assert second.status_code == 402
    assert second.json()["detail"] == "Free scan already used."
    app.dependency_overrides.clear()


def test_sandbox_execute_proxy_success(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_execute(*_args, **_kwargs):
        return {"result": {"stdout": "ok"}, "error": None}

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.sandbox_service, "execute_sandbox_tool", fake_execute)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sandbox/sandbox-123/execute",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={
                "agent_id": "agent-1",
                "tool_name": "terminal_execute",
                "kwargs": {"command": "pwd"},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"result": {"stdout": "ok"}, "error": None}
    app.dependency_overrides.clear()


def test_sandbox_execute_proxy_not_found(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_execute(*_args, **_kwargs):
        raise PermissionError("Sandbox not found or access denied.")

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.sandbox_service, "execute_sandbox_tool", fake_execute)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sandbox/sandbox-missing/execute",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={
                "agent_id": "agent-1",
                "tool_name": "terminal_execute",
                "kwargs": {"command": "pwd"},
            },
        )

    assert response.status_code == 404
    assert response.json()["detail"] == "Sandbox not found or access denied."
    app.dependency_overrides.clear()


def test_sandbox_diffs_proxy_success(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_diffs(*_args, **_kwargs):
        return {"edits": [], "count": 0}

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes.sandbox_service, "fetch_sandbox_diffs", fake_diffs)

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/sandbox/sandbox-123/diffs",
            headers={"Authorization": "Bearer ignored.for.override"},
        )

    assert response.status_code == 200
    assert response.json() == {"edits": [], "count": 0}
    app.dependency_overrides.clear()


def test_sandbox_create_local_upload_succeeds(monkeypatch) -> None:
    """local_upload target type should be accepted by the schema and route."""

    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_check_quota(*_args, **_kwargs):
        return SimpleNamespace(has_quota=True, scans_remaining=5, tokens_remaining=0, message=None)

    async def fake_get_user_plan(_user_id: str) -> str:
        return "pro"

    async def fake_create_sandbox(_request, _user_id):
        return {
            "sandbox_id": "sandbox-upload-1",
            "status": "creating",
            "tool_server_url": None,
            "expires_at": None,
        }

    async def fake_increment_scan_count(*_args, **_kwargs):
        return None

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes, "supabase", _FakeSupabase())
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)
    monkeypatch.setattr(routes.sandbox_service, "create_sandbox", fake_create_sandbox)
    monkeypatch.setattr(routes.usage_service, "increment_scan_count", fake_increment_scan_count)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sandbox",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={
                "scan_id": "scan-upload-1",
                "target": "my-project",
                "target_type": "local_upload",
                "scan_type": "deep",
            },
        )

    assert response.status_code == 200
    assert response.json()["sandbox_id"] == "sandbox-upload-1"
    app.dependency_overrides.clear()


def test_sandbox_create_url_target_still_works(monkeypatch) -> None:
    """Regression: URL target type must continue to work."""

    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_check_quota(*_args, **_kwargs):
        return SimpleNamespace(has_quota=True, scans_remaining=5, tokens_remaining=0, message=None)

    async def fake_get_user_plan(_user_id: str) -> str:
        return "pro"

    async def fake_create_sandbox(_request, _user_id):
        return {
            "sandbox_id": "sandbox-url-1",
            "status": "creating",
            "tool_server_url": None,
            "expires_at": None,
        }

    async def fake_increment_scan_count(*_args, **_kwargs):
        return None

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes, "supabase", _FakeSupabase())
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)
    monkeypatch.setattr(routes.sandbox_service, "create_sandbox", fake_create_sandbox)
    monkeypatch.setattr(routes.usage_service, "increment_scan_count", fake_increment_scan_count)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sandbox",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={
                "scan_id": "scan-url-reg-1",
                "target": "https://example.com",
                "target_type": "url",
                "scan_type": "deep",
            },
        )

    assert response.status_code == 200
    assert response.json()["sandbox_id"] == "sandbox-url-1"
    app.dependency_overrides.clear()


def test_sandbox_create_accepts_standard_scan_mode(monkeypatch) -> None:
    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_check_quota(*_args, **_kwargs):
        return SimpleNamespace(has_quota=True, scans_remaining=5, tokens_remaining=0, message=None)

    async def fake_get_user_plan(_user_id: str) -> str:
        return "pro"

    captured: dict[str, str] = {}

    async def fake_create_sandbox(request, _user_id):
        captured["scan_type"] = request.scan_type
        return {
            "sandbox_id": "sandbox-standard-1",
            "status": "creating",
            "tool_server_url": None,
            "expires_at": None,
        }

    async def fake_increment_scan_count(*_args, **_kwargs):
        return None

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    monkeypatch.setattr(routes, "supabase", _FakeSupabase())
    monkeypatch.setattr(routes.usage_service, "check_quota", fake_check_quota)
    monkeypatch.setattr(routes.usage_service, "get_user_plan", fake_get_user_plan)
    monkeypatch.setattr(routes.sandbox_service, "create_sandbox", fake_create_sandbox)
    monkeypatch.setattr(routes.usage_service, "increment_scan_count", fake_increment_scan_count)

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/sandbox",
            headers={"Authorization": "Bearer ignored.for.override"},
            json={
                "scan_id": "scan-standard-1",
                "target": "https://example.com",
                "target_type": "url",
                "scan_type": "standard",
            },
        )

    assert response.status_code == 200
    assert response.json()["sandbox_id"] == "sandbox-standard-1"
    assert captured["scan_type"] == "standard"
    app.dependency_overrides.clear()
