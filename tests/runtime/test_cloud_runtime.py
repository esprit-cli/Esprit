from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Self

import httpx
import pytest

from esprit.runtime.cloud_runtime import CloudRuntime


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code < 400:
            return
        request = httpx.Request("POST", "https://example.test")
        response = httpx.Response(self.status_code, request=request, text=self.text)
        raise httpx.HTTPStatusError(
            f"HTTP {self.status_code}",
            request=request,
            response=response,
        )


@pytest.mark.asyncio
async def test_create_sandbox_falls_back_to_modern_endpoint_on_405(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, Any] = {
        "legacy_calls": 0,
        "modern_calls": 0,
        "status_calls": 0,
        "modern_payload": None,
    }

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _tb: object,
        ) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> _FakeResponse:
            _ = headers
            if url.endswith("/sandbox/create"):
                state["legacy_calls"] += 1
                return _FakeResponse(405, {"detail": "Method Not Allowed"})
            if url.endswith("/sandbox"):
                state["modern_calls"] += 1
                state["modern_payload"] = json
                return _FakeResponse(
                    200,
                    {
                        "sandbox_id": "sbx-405",
                        "status": "creating",
                        "sandbox_token": "value-1",
                    },
                )
            raise AssertionError(f"Unexpected POST URL: {url}")

        async def get(self, url: str, *, headers: dict[str, str]) -> _FakeResponse:
            _ = headers
            assert url.endswith("/sandbox/sbx-405")
            state["status_calls"] += 1
            if state["status_calls"] == 1:
                return _FakeResponse(200, {"status": "creating"})
            return _FakeResponse(
                200,
                {"status": "running", "tool_server_url": "https://tool.example.test:5443"},
            )

    async def _no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr("esprit.runtime.cloud_runtime.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("esprit.runtime.cloud_runtime.asyncio.sleep", _no_wait)

    runtime = CloudRuntime(access_token="access-token", api_base="https://api.example.test")
    result = await runtime.create_sandbox(agent_id="agent-1")

    assert state["legacy_calls"] == 1
    assert state["modern_calls"] == 1
    assert state["status_calls"] == 2

    modern_payload = state["modern_payload"]
    assert modern_payload["scan_id"] == "cli-agent-1"
    assert modern_payload["scan_type"] == "quick"
    assert modern_payload["target_type"] == "url"

    assert result["workspace_id"] == "sbx-405"
    assert result["api_url"] == "https://tool.example.test:5443"
    assert result["tool_server_port"] == 5443
    returned_auth = result["auth_token"]
    assert returned_auth is not None
    assert returned_auth != "access-token"
    assert runtime._sandboxes["sbx-405"]["auth_token"] == returned_auth


@pytest.mark.asyncio
async def test_create_sandbox_uses_local_upload_shape_for_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state: dict[str, Any] = {"modern_payload": None}

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _tb: object,
        ) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> _FakeResponse:
            _ = headers
            if url.endswith("/sandbox/create"):
                return _FakeResponse(404, {"detail": "Not Found"})
            if url.endswith("/sandbox"):
                state["modern_payload"] = json
                return _FakeResponse(
                    200,
                    {
                        "sandbox_id": "sbx-local",
                        "status": "running",
                        "tool_server_url": "https://tool.example.test",
                    },
                )
            raise AssertionError(f"Unexpected POST URL: {url}")

        async def get(self, _url: str, *, headers: dict[str, str]) -> _FakeResponse:
            _ = headers
            raise AssertionError("Status polling should not run when tool_server_url is present")

    monkeypatch.setattr("esprit.runtime.cloud_runtime.httpx.AsyncClient", FakeAsyncClient)

    runtime = CloudRuntime(access_token="access-token", api_base="https://api.example.test")
    result = await runtime.create_sandbox(
        agent_id="agent-local",
        local_sources=[
            {"source_path": "/tmp/workspace", "workspace_subdir": "workspace"},
        ],
    )

    modern_payload = state["modern_payload"]
    assert modern_payload["target"] == "workspace"
    assert modern_payload["target_type"] == "local_upload"
    assert result["workspace_id"] == "sbx-local"
    assert result["api_url"] == "https://tool.example.test"


@pytest.mark.asyncio
async def test_track_and_untrack_sandbox_state_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "cloud_state.json"
    monkeypatch.setenv("ESPRIT_CLOUD_SANDBOX_STATE_FILE", str(state_file))

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _tb: object,
        ) -> None:
            return None

        async def post(
            self,
            url: str,
            *,
            json: dict[str, Any],
            headers: dict[str, str],
        ) -> _FakeResponse:
            _ = (json, headers)
            if url.endswith("/sandbox/create"):
                return _FakeResponse(
                    200,
                    {
                        "sandbox_id": "sbx-track-1",
                        "sandbox_token": "sandbox-token",
                        "tool_server_url": "https://tool.example.test",
                    },
                )
            raise AssertionError(f"Unexpected POST URL: {url}")

        async def delete(self, url: str, *, headers: dict[str, str]) -> _FakeResponse:
            _ = headers
            if url.endswith("/sandbox/sbx-track-1"):
                return _FakeResponse(204, {})
            raise AssertionError(f"Unexpected DELETE URL: {url}")

    monkeypatch.setattr("esprit.runtime.cloud_runtime.httpx.AsyncClient", FakeAsyncClient)

    runtime = CloudRuntime(access_token="access-token", api_base="https://api.example.test")
    await runtime.create_sandbox(agent_id="agent-track")

    payload_after_create = json.loads(state_file.read_text(encoding="utf-8"))
    sandboxes_after_create = payload_after_create.get("sandboxes", [])
    assert len(sandboxes_after_create) == 1
    assert sandboxes_after_create[0]["sandbox_id"] == "sbx-track-1"
    assert sandboxes_after_create[0]["api_base"] == "https://api.example.test"

    await runtime.destroy_sandbox("sbx-track-1")

    payload_after_destroy = json.loads(state_file.read_text(encoding="utf-8"))
    assert payload_after_destroy.get("sandboxes", []) == []


@pytest.mark.asyncio
async def test_cleanup_stale_sandboxes_reaps_previous_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "cloud_state.json"
    monkeypatch.setenv("ESPRIT_CLOUD_SANDBOX_STATE_FILE", str(state_file))
    state_file.write_text(
        json.dumps(
            {
                "version": 1,
                "sandboxes": [
                    {"sandbox_id": "stale-1", "api_base": "https://api.example.test", "created_at": "1"},
                    {"sandbox_id": "stale-2", "api_base": "https://api.example.test", "created_at": "2"},
                    {"sandbox_id": "other-env", "api_base": "https://other.example.test", "created_at": "3"},
                ],
            }
        ),
        encoding="utf-8",
    )

    delete_calls: list[str] = []

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            _exc_type: type[BaseException] | None,
            _exc: BaseException | None,
            _tb: object,
        ) -> None:
            return None

        async def delete(self, url: str, *, headers: dict[str, str]) -> _FakeResponse:
            _ = headers
            delete_calls.append(url)
            if url.endswith("/sandbox/stale-1"):
                return _FakeResponse(204, {})
            return _FakeResponse(500, {"detail": "error"})

    monkeypatch.setattr("esprit.runtime.cloud_runtime.httpx.AsyncClient", FakeAsyncClient)

    cleaned = await CloudRuntime.cleanup_stale_sandboxes(
        access_token="access-token",
        api_base="https://api.example.test",
    )

    assert cleaned == 1
    assert delete_calls == [
        "https://api.example.test/sandbox/stale-1",
        "https://api.example.test/sandbox/stale-2",
    ]

    final_payload = json.loads(state_file.read_text(encoding="utf-8"))
    final_sandboxes = final_payload.get("sandboxes", [])
    assert {entry["sandbox_id"] for entry in final_sandboxes} == {"stale-2", "other-env"}
