"""Tests for patch download URL resolution."""

from __future__ import annotations

from types import SimpleNamespace

from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

import app.core.auth as auth_module
from app.api import routes
from app.main import app


def _fake_user() -> auth_module.TokenPayload:
    return auth_module.TokenPayload(
        sub="user-123",
        email="patch@esprit.dev",
        role="authenticated",
        exp=0,
    )


class _FakeScanQuery:
    def __init__(self, scan: dict) -> None:
        self.scan = scan

    def select(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return self

    def eq(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return self

    def single(self):  # type: ignore[no-untyped-def]
        return self

    def execute(self):  # type: ignore[no-untyped-def]
        return SimpleNamespace(data=self.scan)


class _FakeSupabase:
    def __init__(self, scan: dict) -> None:
        self.scan = scan

    def table(self, _name: str):  # type: ignore[no-untyped-def]
        return _FakeScanQuery(self.scan)


class _FakeS3Client:
    def __init__(self, existing_keys: set[str]) -> None:
        self.existing_keys = existing_keys

    def head_object(self, **kwargs):  # type: ignore[no-untyped-def]
        key = kwargs.get("Key")
        if key not in self.existing_keys:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def generate_presigned_url(self, _operation: str, Params: dict, ExpiresIn: int = 3600):  # type: ignore[no-untyped-def]
        _ = ExpiresIn
        return f"https://download.example/{Params['Key']}"


def _install_common_overrides(monkeypatch, scan_id: str) -> None:
    scan = {"id": scan_id, "user_id": "user-123", "status": "completed"}
    monkeypatch.setattr(routes, "supabase", _FakeSupabase(scan))
    monkeypatch.setattr(routes.settings, "s3_bucket", "scan-artifacts")
    monkeypatch.setattr(routes.settings, "aws_region", "us-east-1")
    monkeypatch.setattr(routes.settings, "aws_access_key_id", "test")
    monkeypatch.setattr(routes.settings, "aws_secret_access_key", "test")


def test_patch_url_prefers_user_scoped_key(monkeypatch) -> None:
    scan_id = "00000000-0000-0000-0000-000000000aaa"
    _install_common_overrides(monkeypatch, scan_id)

    keys = {f"patches/user-123/{scan_id}.patch"}
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: _FakeS3Client(keys))

    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/scans/{scan_id}/patch",
                headers={"Authorization": "Bearer ignored.for.override"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_patch"] is True
    assert payload["download_url"].endswith(f"/patches/user-123/{scan_id}.patch")


def test_patch_url_falls_back_to_legacy_key(monkeypatch) -> None:
    scan_id = "00000000-0000-0000-0000-000000000bbb"
    _install_common_overrides(monkeypatch, scan_id)

    keys = {f"patches/{scan_id}.patch"}
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: _FakeS3Client(keys))

    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    try:
        with TestClient(app) as client:
            response = client.get(
                f"/api/v1/scans/{scan_id}/patch",
                headers={"Authorization": "Bearer ignored.for.override"},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload["has_patch"] is True
    assert payload["download_url"].endswith(f"/patches/{scan_id}.patch")
