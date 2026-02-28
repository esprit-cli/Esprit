"""Tests for SandboxService task listing, ownership, and stop behavior."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services.sandbox_service import SandboxService


class _FakeECSClient:
    def __init__(self) -> None:
        self.stop_calls: list[str] = []
        self._task_pages = {
            "RUNNING": [{"taskArns": ["arn:task/run-1", "arn:task/run-2"]}],
            "PENDING": [{"taskArns": ["arn:task/pending-1"]}],
        }
        self._task_tags = {
            "arn:task/run-1": [{"key": "ScanId", "value": "scan-1"}, {"key": "UserId", "value": "user-1"}],
            "arn:task/run-2": [{"key": "ScanId", "value": "scan-2"}, {"key": "UserId", "value": "user-2"}],
            "arn:task/pending-1": [{"key": "ScanId", "value": "scan-1"}, {"key": "UserId", "value": "user-1"}],
        }

    def get_paginator(self, operation: str):  # type: ignore[no-untyped-def]
        assert operation == "list_tasks"

        class _DispatchPaginator:
            def __init__(self, outer: _FakeECSClient):
                self.outer = outer

            def paginate(self, **kwargs):  # type: ignore[no-untyped-def]
                status = kwargs["desiredStatus"]
                return self.outer._task_pages.get(status, [])

        return _DispatchPaginator(self)

    def describe_tasks(self, **kwargs):  # type: ignore[no-untyped-def]
        tasks = []
        for arn in kwargs.get("tasks", []):
            tags = self._task_tags.get(arn, [])
            tasks.append(
                {
                    "taskArn": arn,
                    "lastStatus": "RUNNING" if "run" in arn else "PENDING",
                    "attachments": [],
                    "tags": tags,
                    "startedAt": datetime.now(tz=timezone.utc),
                }
            )
        return {"tasks": tasks}

    def list_tags_for_resource(self, resourceArn: str):  # type: ignore[no-untyped-def]
        return {"tags": self._task_tags.get(resourceArn, [])}

    def stop_task(self, **kwargs):  # type: ignore[no-untyped-def]
        self.stop_calls.append(kwargs["task"])
        return {"task": {"taskArn": kwargs["task"]}}


class _FakeEC2Client:
    def describe_network_interfaces(self, **_kwargs):  # type: ignore[no-untyped-def]
        return {"NetworkInterfaces": []}


def _build_service() -> SandboxService:
    service = SandboxService.__new__(SandboxService)
    service.ecs_client = _FakeECSClient()  # type: ignore[attr-defined]
    service.ec2_client = _FakeEC2Client()  # type: ignore[attr-defined]
    return service


def test_stop_tasks_for_scan_handles_running_and_pending() -> None:
    service = _build_service()
    stopped = asyncio.run(service.stop_tasks_for_scan("scan-1"))
    assert stopped == 2
    assert sorted(service.ecs_client.stop_calls) == ["arn:task/pending-1", "arn:task/run-1"]  # type: ignore[attr-defined]


def test_destroy_sandbox_enforces_user_ownership() -> None:
    service = _build_service()
    # SandboxId not present in tags map by default; inject for this test.
    service.ecs_client._task_tags["arn:task/run-1"].append({"key": "SandboxId", "value": "sandbox-1"})  # type: ignore[attr-defined]

    denied = asyncio.run(service.destroy_sandbox("sandbox-1", "different-user"))
    allowed = asyncio.run(service.destroy_sandbox("sandbox-1", "user-1"))

    assert denied is False
    assert allowed is True


def test_get_sandbox_status_returns_running_only_for_owner() -> None:
    service = _build_service()
    service.ecs_client._task_tags["arn:task/run-1"].append({"key": "SandboxId", "value": "sandbox-owned"})  # type: ignore[attr-defined]

    owner_status = asyncio.run(service.get_sandbox_status("sandbox-owned", "user-1"))
    other_status = asyncio.run(service.get_sandbox_status("sandbox-owned", "user-2"))

    assert owner_status is not None
    assert owner_status.status in {"running", "creating"}
    assert other_status is not None
    assert other_status.status == "stopped"
