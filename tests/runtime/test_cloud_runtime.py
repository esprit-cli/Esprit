"""Tests for cloud runtime sandbox payload wiring."""

import asyncio
from typing import Any

from esprit.runtime.cloud_runtime import CloudRuntime


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeAsyncClient:
    def __init__(self, recorder: dict[str, Any]) -> None:
        self._recorder = recorder

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False

    async def post(self, url: str, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
        self._recorder["url"] = url
        self._recorder["json"] = json
        self._recorder["headers"] = headers
        return _FakeResponse(
            {
                "sandbox_id": "sbx_1",
                "api_url": "https://api.example.com/sandbox/sbx_1",
                "tool_server_port": 443,
                "auth_token": "sandbox-token",
            }
        )


class TestCloudRuntimePayload:
    def test_create_sandbox_sends_sources_and_artifacts(self, monkeypatch: Any) -> None:
        recorder: dict[str, Any] = {}
        monkeypatch.setattr(
            "esprit.runtime.cloud_runtime.httpx.AsyncClient",
            lambda *args, **kwargs: _FakeAsyncClient(recorder),
        )

        runtime = CloudRuntime(access_token="access-token", api_base="https://api.example.com")
        asyncio.run(
            runtime.create_sandbox(
                agent_id="agent_1",
                local_sources=[{"source_path": "/tmp/src"}],
                artifacts=[{"source_path": "/tmp/app.apk", "workspace_subdir": "mobile-app"}],
            )
        )

        payload = recorder["json"]
        assert payload["agent_id"] == "agent_1"
        assert payload["local_sources"][0]["source_path"] == "/tmp/src"
        assert payload["local_artifacts"][0]["source_path"] == "/tmp/app.apk"
        assert payload["local_artifacts"][0]["workspace_subdir"] == "mobile-app"
