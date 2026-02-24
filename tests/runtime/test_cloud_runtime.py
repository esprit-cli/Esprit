"""Tests for esprit.runtime.cloud_runtime module."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from esprit.runtime import SandboxInitializationError
from esprit.runtime.cloud_runtime import CloudRuntime


class _FakeAsyncClient:
    """Small async context manager wrapper for mocked HTTP calls."""

    def __init__(
        self,
        post: AsyncMock | None = None,
        delete: AsyncMock | None = None,
    ) -> None:
        self.post = post or AsyncMock()
        self.delete = delete or AsyncMock()

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        return False


def _response(status_code: int, payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    response.raise_for_status.side_effect = None
    return response


@pytest.mark.asyncio
async def test_create_sandbox_success() -> None:
    runtime = CloudRuntime(access_token="token-123", api_base="https://api.example.com/v1")

    create_response = _response(
        200,
        {
            "sandbox_id": "sbx-1",
            "api_url": "https://runtime.example.com/sandbox/sbx-1",
            "auth_token": "sandbox-token",
            "tool_server_port": 443,
        },
    )
    mock_post = AsyncMock(return_value=create_response)

    with patch(
        "esprit.runtime.cloud_runtime.httpx.AsyncClient",
        return_value=_FakeAsyncClient(post=mock_post),
    ):
        sandbox = await runtime.create_sandbox(agent_id="agent-1")

    assert sandbox["workspace_id"] == "sbx-1"
    assert sandbox["api_url"] == "https://runtime.example.com/sandbox/sbx-1"
    assert sandbox["auth_token"] == "sandbox-token"
    assert sandbox["tool_server_port"] == 443
    assert sandbox["agent_id"] == "agent-1"
    assert runtime._sandboxes["sbx-1"]["agent_id"] == "agent-1"


@pytest.mark.asyncio
async def test_create_sandbox_invalid_response_raises() -> None:
    runtime = CloudRuntime(access_token="token-123", api_base="https://api.example.com/v1")
    create_response = _response(200, {"api_url": "https://runtime.example.com/sandbox/missing"})
    mock_post = AsyncMock(return_value=create_response)

    with patch(
        "esprit.runtime.cloud_runtime.httpx.AsyncClient",
        return_value=_FakeAsyncClient(post=mock_post),
    ):
        with pytest.raises(SandboxInitializationError) as exc_info:
            await runtime.create_sandbox(agent_id="agent-1")

    assert "Invalid cloud sandbox response" in str(exc_info.value)


@pytest.mark.asyncio
async def test_create_sandbox_requires_sandbox_token() -> None:
    runtime = CloudRuntime(access_token="token-123", api_base="https://api.example.com/v1")
    create_response = _response(
        200,
        {
            "sandbox_id": "sbx-1",
            "api_url": "https://runtime.example.com/sandbox/sbx-1",
            "tool_server_port": 443,
        },
    )
    mock_post = AsyncMock(return_value=create_response)

    with patch(
        "esprit.runtime.cloud_runtime.httpx.AsyncClient",
        return_value=_FakeAsyncClient(post=mock_post),
    ):
        with pytest.raises(SandboxInitializationError) as exc_info:
            await runtime.create_sandbox(agent_id="agent-1")

    assert "sandbox auth token" in (exc_info.value.details or "")


@pytest.mark.asyncio
async def test_create_sandbox_http_error_raises() -> None:
    runtime = CloudRuntime(access_token="token-123", api_base="https://api.example.com/v1")

    request = httpx.Request("POST", "https://api.example.com/v1/sandbox/create")
    response = httpx.Response(401, request=request, text="unauthorized")

    http_error_response = MagicMock()
    http_error_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "boom",
        request=request,
        response=response,
    )
    http_error_response.status_code = 401
    http_error_response.text = "unauthorized"
    mock_post = AsyncMock(return_value=http_error_response)

    with patch(
        "esprit.runtime.cloud_runtime.httpx.AsyncClient",
        return_value=_FakeAsyncClient(post=mock_post),
    ):
        with pytest.raises(SandboxInitializationError) as exc_info:
            await runtime.create_sandbox(agent_id="agent-1")

    assert "Failed to create cloud sandbox" in str(exc_info.value)


@pytest.mark.asyncio
async def test_destroy_sandbox_removes_local_state() -> None:
    runtime = CloudRuntime(access_token="token-123", api_base="https://api.example.com/v1")
    runtime._sandboxes["sbx-1"] = {
        "api_url": "https://runtime.example.com/sandbox/sbx-1",
        "tool_server_port": 443,
        "agent_id": "agent-1",
    }

    delete_response = _response(204, {})
    mock_delete = AsyncMock(return_value=delete_response)

    with patch(
        "esprit.runtime.cloud_runtime.httpx.AsyncClient",
        return_value=_FakeAsyncClient(delete=mock_delete),
    ):
        await runtime.destroy_sandbox("sbx-1")

    assert "sbx-1" not in runtime._sandboxes


@pytest.mark.asyncio
async def test_get_sandbox_url_falls_back_to_api_base() -> None:
    runtime = CloudRuntime(access_token="token-123", api_base="https://api.example.com/v1")

    url = await runtime.get_sandbox_url("unknown-sbx", 443)

    assert url == "https://api.example.com/v1/sandbox/unknown-sbx"


@pytest.mark.asyncio
async def test_create_sandbox_rejects_untrusted_runtime_url() -> None:
    runtime = CloudRuntime(access_token="token-123", api_base="https://api.example.com/v1")

    create_response = _response(
        200,
        {
            "sandbox_id": "sbx-1",
            "api_url": "https://attacker.example.net/sandbox/sbx-1",
            "auth_token": "sandbox-token",
            "tool_server_port": 443,
        },
    )
    mock_post = AsyncMock(return_value=create_response)

    with patch(
        "esprit.runtime.cloud_runtime.httpx.AsyncClient",
        return_value=_FakeAsyncClient(post=mock_post),
    ):
        with pytest.raises(SandboxInitializationError) as exc_info:
            await runtime.create_sandbox(agent_id="agent-1")

        assert "untrusted api_url host" in (exc_info.value.details or "")


def test_sanitize_local_sources_strips_path_metadata() -> None:
    local_sources = [
        {
            "source_path": "../../../../../etc/passwd",
            "workspace_subdir": "../unsafe-dir///..",
        }
    ]

    sanitized = CloudRuntime._sanitize_local_sources(local_sources)

    assert sanitized == [{"source_path": "passwd", "workspace_subdir": "unsafe-dir"}]
