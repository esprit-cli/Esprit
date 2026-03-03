"""Regression tests for malformed scan ids on scan routes."""

from __future__ import annotations

from fastapi.testclient import TestClient

import app.core.auth as auth_module
from app.api import routes
from app.main import app


class _FailIfQueriedSupabase:
    def table(self, _name: str):  # type: ignore[no-untyped-def]
        raise AssertionError("Supabase should not be queried for malformed scan ids")


def _fake_user() -> auth_module.TokenPayload:
    return auth_module.TokenPayload(
        sub="user-123",
        email="scanid@esprit.dev",
        role="authenticated",
        exp=0,
    )


def test_scan_status_malformed_scan_id_returns_not_found(monkeypatch) -> None:
    monkeypatch.setattr(routes, "supabase", _FailIfQueriedSupabase())

    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    try:
        with TestClient(app) as client:
            response = client.get(
                "/api/v1/scans/not-a-uuid",
                headers={"Authorization": "Bearer ignored.for.override"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 404
    assert response.json()["detail"] == "Scan not found"

