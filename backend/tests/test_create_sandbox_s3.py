"""Tests for create_sandbox S3 env var passing and local_upload target type."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.models.schemas import SandboxCreateRequest
from app.services.sandbox_service import SandboxService


class _CapturingECSClient:
    """Fake ECS client that captures run_task() kwargs."""

    def __init__(self) -> None:
        self.run_task_calls: list[dict] = []
        self._task_pages: dict[str, list] = {
            "RUNNING": [{"taskArns": []}],
            "PENDING": [{"taskArns": []}],
        }

    def run_task(self, **kwargs) -> dict:  # type: ignore[no-untyped-def]
        self.run_task_calls.append(kwargs)
        return {"tasks": [{"taskArn": "arn:aws:ecs:us-east-1:123:task/test-task"}]}

    def get_paginator(self, operation: str):  # type: ignore[no-untyped-def]
        class _P:
            def __init__(self, outer: _CapturingECSClient):
                self.outer = outer

            def paginate(self, **kwargs):  # type: ignore[no-untyped-def]
                status = kwargs["desiredStatus"]
                return self.outer._task_pages.get(status, [])

        return _P(self)


class _FakeEC2Client:
    def describe_network_interfaces(self, **_kwargs):  # type: ignore[no-untyped-def]
        return {"NetworkInterfaces": []}


def _build_capturing_service() -> tuple[SandboxService, _CapturingECSClient]:
    service = SandboxService.__new__(SandboxService)
    ecs = _CapturingECSClient()
    service.ecs_client = ecs  # type: ignore[attr-defined]
    service.ec2_client = _FakeEC2Client()  # type: ignore[attr-defined]
    service._recent_sandboxes = {}  # type: ignore[attr-defined]
    return service, ecs


def _env_dict(ecs_client: _CapturingECSClient) -> dict[str, str]:
    """Extract environment vars from the last run_task call as a dict."""
    call = ecs_client.run_task_calls[-1]
    env_list = call["overrides"]["containerOverrides"][0]["environment"]
    return {e["name"]: e["value"] for e in env_list}


def test_create_sandbox_passes_s3_env_for_local_upload() -> None:
    service, ecs = _build_capturing_service()
    request = SandboxCreateRequest(
        scan_id="scan-local-1",
        target="my-project",
        target_type="local_upload",
        scan_type="deep",
    )
    asyncio.run(service.create_sandbox(request, "user-1"))

    env = _env_dict(ecs)
    assert env["UPLOAD_S3_KEY"] == "uploads/user-1/scan-local-1.tar.gz"
    assert "S3_BUCKET" in env  # Present even if empty (settings default)
    assert "AWS_DEFAULT_REGION" in env
    assert "AWS_ACCESS_KEY_ID" in env
    assert "AWS_SECRET_ACCESS_KEY" in env


def test_create_sandbox_omits_s3_key_for_url_target() -> None:
    service, ecs = _build_capturing_service()
    request = SandboxCreateRequest(
        scan_id="scan-url-1",
        target="https://example.com",
        target_type="url",
        scan_type="quick",
    )
    asyncio.run(service.create_sandbox(request, "user-1"))

    env = _env_dict(ecs)
    assert env["UPLOAD_S3_KEY"] == ""
    # S3_BUCKET is still present (just empty or filled, doesn't matter)
    assert "S3_BUCKET" in env


def test_create_sandbox_omits_s3_key_for_repository_target() -> None:
    service, ecs = _build_capturing_service()
    request = SandboxCreateRequest(
        scan_id="scan-repo-1",
        target="https://github.com/user/repo",
        target_type="repository",
        scan_type="deep",
    )
    asyncio.run(service.create_sandbox(request, "user-1"))

    env = _env_dict(ecs)
    assert env["UPLOAD_S3_KEY"] == ""


def test_create_sandbox_preserves_existing_env_vars() -> None:
    """Regression: existing env vars (SCAN_ID, TARGET_TYPE, etc.) must still be present."""
    service, ecs = _build_capturing_service()
    request = SandboxCreateRequest(
        scan_id="scan-regression-1",
        target="https://example.com",
        target_type="url",
        scan_type="quick",
    )
    asyncio.run(service.create_sandbox(request, "user-1"))

    env = _env_dict(ecs)
    assert env["SCAN_ID"] == "scan-regression-1"
    assert env["TARGET"] == "https://example.com"
    assert env["TARGET_TYPE"] == "url"
    assert env["SCAN_TYPE"] == "quick"
    assert env["USER_ID"] == "user-1"
    assert "SANDBOX_ID" in env
    assert "TOOL_SERVER_TOKEN" in env
    assert "LLM_PROXY_URL" in env
    assert env["TEST_USERNAME"] == ""
    assert env["TEST_PASSWORD"] == ""


def test_create_sandbox_sets_lineage_env_and_tags_for_child_sandboxes() -> None:
    service, ecs = _build_capturing_service()
    request = SandboxCreateRequest(
        scan_id="scan-child-1",
        target="https://example.com",
        target_type="url",
        scan_type="quick",
        parent_sandbox_id="sandbox-parent",
        root_sandbox_id="sandbox-root",
    )

    asyncio.run(service.create_sandbox(request, "user-1"))

    env = _env_dict(ecs)
    assert env["PARENT_SANDBOX_ID"] == "sandbox-parent"
    assert env["ROOT_SANDBOX_ID"] == "sandbox-root"

    tags = {entry["key"]: entry["value"] for entry in ecs.run_task_calls[-1]["tags"]}
    assert tags["ParentSandboxId"] == "sandbox-parent"
    assert tags["RootSandboxId"] == "sandbox-root"
