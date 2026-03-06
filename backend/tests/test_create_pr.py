"""Tests for patch-driven PR creation route."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.core.auth as auth_module
from app.api import routes
from app.main import app
from app.services.pr_service import PreparedPRBranch, PullRequestResult


def _fake_user() -> auth_module.TokenPayload:
    return auth_module.TokenPayload(
        sub="user-pr-1",
        email="pr@esprit.dev",
        role="authenticated",
        exp=0,
    )


class _FakeTableQuery:
    def __init__(self, store: dict[str, dict]) -> None:
        self.store = store
        self.eq_id: str | None = None
        self.operation = "select"
        self.update_payload: dict | None = None
        self.expect_single = False

    def select(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        self.operation = "select"
        return self

    def update(self, payload: dict):  # type: ignore[no-untyped-def]
        self.operation = "update"
        self.update_payload = payload
        return self

    def eq(self, field: str, value: str):  # type: ignore[no-untyped-def]
        if field == "id":
            self.eq_id = value
        return self

    def single(self):  # type: ignore[no-untyped-def]
        self.expect_single = True
        return self

    def execute(self):  # type: ignore[no-untyped-def]
        row = self.store.get(self.eq_id or "")
        if self.operation == "update":
            assert row is not None
            assert self.update_payload is not None
            row.update(self.update_payload)
            return SimpleNamespace(data=[dict(row)])
        if self.expect_single:
            return SimpleNamespace(data=dict(row) if row else None)
        return SimpleNamespace(data=[dict(row)] if row else [])


class _FakeSupabase:
    def __init__(self, scans: dict[str, dict], profiles: dict[str, dict]) -> None:
        self.scans = scans
        self.profiles = profiles

    def table(self, name: str):  # type: ignore[no-untyped-def]
        if name == "scans":
            return _FakeTableQuery(self.scans)
        if name == "profiles":
            return _FakeTableQuery(self.profiles)
        raise AssertionError(f"Unexpected table {name}")


def test_create_pr_uses_patch_artifact_without_fix_branch_metadata(monkeypatch) -> None:
    scan_id = "00000000-0000-0000-0000-000000000222"
    scans = {
        scan_id: {
            "id": scan_id,
            "user_id": "user-pr-1",
            "status": "completed",
            "target": "https://github.com/acme/widgets",
            "target_type": "repository",
            "has_modified_files": False,
            "vulnerabilities_found": 3,
            "critical_count": 1,
            "high_count": 1,
            "medium_count": 1,
            "low_count": 0,
            "pr_metadata": {},
            "repo_branch": None,
        }
    }
    profiles = {"user-pr-1": {"id": "user-pr-1", "github_app_installation_id": 99}}

    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_installation_token(_installation_id: int) -> str:
        return "ghs_test"

    async def fake_default_branch(_repo_full_name: str, _github_token: str) -> str:
        return "main"

    monkeypatch.setattr(routes, "supabase", _FakeSupabase(scans, profiles))
    monkeypatch.setattr(routes, "get_github_app_installation_token", fake_installation_token)
    monkeypatch.setattr(routes.pr_service, "resolve_patch_s3_key", lambda _scan, _user_id: f"patches/user-pr-1/{scan_id}.patch")
    monkeypatch.setattr(routes.pr_service, "download_patch_text", lambda _key: "--- app.py\n+++ app.py\n@@ -1 +1 @@\n-hi\n+bye\n")
    monkeypatch.setattr(routes.pr_service, "get_repo_default_branch", fake_default_branch)
    monkeypatch.setattr(
        routes.pr_service,
        "prepare_fix_branch",
        lambda **_kwargs: PreparedPRBranch(
            branch_name=f"esprit/security-fixes-{scan_id[:8]}",
            base_branch="main",
            patch_s3_key=f"patches/user-pr-1/{scan_id}.patch",
            modified_files_count=2,
            commit_sha="abc123",
        ),
    )

    async def fake_create_pull_request(**_kwargs) -> PullRequestResult:
        return PullRequestResult(
            pr_url="https://github.com/acme/widgets/pull/12",
            pr_number=12,
        )

    monkeypatch.setattr(routes.pr_service, "create_pull_request", fake_create_pull_request)

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/scans/{scan_id}/create-pr",
                headers={"Authorization": "Bearer ignored.for.override"},
                json={},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "success": True,
        "pr_url": "https://github.com/acme/widgets/pull/12",
        "pr_number": 12,
        "error": None,
    }
    assert scans[scan_id]["has_modified_files"] is True
    assert scans[scan_id]["pr_metadata"]["fix_branch"] == f"esprit/security-fixes-{scan_id[:8]}"
    assert scans[scan_id]["pr_metadata"]["commit_sha"] == "abc123"
    assert scans[scan_id]["pr_metadata"]["repo_full_name"] == "acme/widgets"


def test_create_pr_rejects_non_repository_scan(monkeypatch) -> None:
    scan_id = "00000000-0000-0000-0000-000000000333"
    scans = {
        scan_id: {
            "id": scan_id,
            "user_id": "user-pr-1",
            "status": "completed",
            "target": "https://example.com",
            "target_type": "url",
            "pr_metadata": {},
        }
    }
    profiles = {"user-pr-1": {"id": "user-pr-1", "github_app_installation_id": 99}}

    async def fake_user() -> auth_module.TokenPayload:
        return _fake_user()

    async def fake_installation_token(_installation_id: int) -> str:
        return "ghs_test"

    monkeypatch.setattr(routes, "supabase", _FakeSupabase(scans, profiles))
    monkeypatch.setattr(routes, "get_github_app_installation_token", fake_installation_token)

    app.dependency_overrides[auth_module.get_current_user] = fake_user
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/api/v1/scans/{scan_id}/create-pr",
                headers={"Authorization": "Bearer ignored.for.override"},
                json={},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "pr_url": None,
        "pr_number": None,
        "error": "Pull requests are only available for GitHub repository scans.",
    }
