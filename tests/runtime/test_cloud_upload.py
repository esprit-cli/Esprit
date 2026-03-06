from __future__ import annotations

import tarfile
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any, Self

import httpx
import pytest

from esprit.interface.utils import upload_local_sources
from esprit.runtime.cloud_runtime import CloudRuntime


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)

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


def test_upload_local_sources_creates_tar_and_uploads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Create temp dir with files, mock httpx, verify tar + presigned URL flow."""
    src = tmp_path / "project"
    src.mkdir()
    (src / "main.py").write_text("print('hello')")
    (src / "lib").mkdir()
    (src / "lib" / "util.py").write_text("pass")

    uploaded: dict[str, Any] = {"content": None, "presigned_called": False, "put_called": False}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            uploaded["presigned_called"] = True
            assert kwargs["json"]["scan_id"]
            return _FakeResponse(200, {"upload_url": "https://s3.example.test/upload", "s3_key": "uploads/test.tar.gz"})

        def put(self, url: str, **kwargs: Any) -> _FakeResponse:
            uploaded["put_called"] = True
            uploaded["content"] = kwargs.get("content")
            assert kwargs["headers"]["Content-Type"] == "application/gzip"
            return _FakeResponse(200, {})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    s3_key = upload_local_sources(
        local_sources=[{"source_path": str(src), "workspace_subdir": "workspace"}],
        scan_id="test-scan-id",
        api_url="https://api.example.test",
        api_token="token",
    )

    assert s3_key == "uploads/test.tar.gz"
    assert uploaded["presigned_called"]
    assert uploaded["put_called"]

    # Verify the tar.gz content
    tar_buffer = BytesIO(uploaded["content"])
    with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
        names = sorted(tar.getnames())
    assert "workspace/main.py" in names
    assert "workspace/lib/util.py" in names


def test_upload_local_sources_excludes_node_modules_and_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ensure EXCLUDE_DIRS filtering works."""
    src = tmp_path / "project"
    src.mkdir()
    (src / "app.js").write_text("console.log('hi')")
    (src / "node_modules").mkdir()
    (src / "node_modules" / "pkg.js").write_text("module")
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("[core]")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "mod.pyc").write_bytes(b"\x00")

    uploaded: dict[str, Any] = {"content": None}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(200, {"upload_url": "https://s3.example.test/upload", "s3_key": "key"})

        def put(self, url: str, **kwargs: Any) -> _FakeResponse:
            uploaded["content"] = kwargs.get("content")
            return _FakeResponse(200, {})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    upload_local_sources(
        local_sources=[{"source_path": str(src), "workspace_subdir": "src"}],
        scan_id="scan-1",
        api_url="https://api.example.test",
        api_token="token",
    )

    tar_buffer = BytesIO(uploaded["content"])
    with tarfile.open(fileobj=tar_buffer, mode="r:gz") as tar:
        names = tar.getnames()

    assert "src/app.js" in names
    assert not any("node_modules" in n for n in names)
    assert not any(".git" in n for n in names)
    assert not any("__pycache__" in n for n in names)


def test_upload_local_sources_skips_nonexistent_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nonexistent source_path should not cause failure; presigned URL still requested."""
    uploaded: dict[str, bool] = {"presigned_called": False}

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            uploaded["presigned_called"] = True
            return _FakeResponse(200, {"upload_url": "https://s3.example.test/upload", "s3_key": "key"})

        def put(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(200, {})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    s3_key = upload_local_sources(
        local_sources=[{"source_path": "/nonexistent/path/abc123", "workspace_subdir": "ws"}],
        scan_id="scan-2",
        api_url="https://api.example.test",
        api_token="token",
    )

    assert s3_key == "key"
    assert uploaded["presigned_called"]


def test_upload_local_sources_presigned_url_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """403 from presigned URL endpoint should raise HTTPStatusError."""

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(403, {"detail": "Forbidden"})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(httpx.HTTPStatusError):
        upload_local_sources(
            local_sources=[],
            scan_id="scan-3",
            api_url="https://api.example.test",
            api_token="token",
        )


def test_upload_local_sources_s3_put_failure_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """500 from S3 PUT should raise HTTPStatusError."""

    class FakeClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args: Any) -> None:
            pass

        def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(200, {"upload_url": "https://s3.example.test/upload", "s3_key": "key"})

        def put(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse(500, {"detail": "Internal Server Error"})

    monkeypatch.setattr(httpx, "Client", FakeClient)
    with pytest.raises(httpx.HTTPStatusError):
        upload_local_sources(
            local_sources=[],
            scan_id="scan-4",
            api_url="https://api.example.test",
            api_token="token",
        )


@pytest.mark.asyncio
async def test_create_sandbox_uploads_before_api_call_for_local_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify upload_local_sources is called before sandbox API POST when local_sources present."""
    call_order: list[str] = []
    state: dict[str, Any] = {"modern_payload": None}

    def fake_upload(**kwargs: Any) -> str:
        call_order.append("upload")
        return "uploads/fake.tar.gz"

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
            _ = headers
            call_order.append(f"post:{url.split('/')[-1]}")
            if url.endswith("/sandbox/create"):
                return _FakeResponse(404, {"detail": "Not Found"})
            if url.endswith("/sandbox"):
                state["modern_payload"] = json
                return _FakeResponse(
                    200,
                    {
                        "sandbox_id": "sbx-upload-test",
                        "status": "running",
                        "tool_server_url": "https://tool.example.test",
                    },
                )
            raise AssertionError(f"Unexpected POST URL: {url}")

        async def get(self, _url: str, *, headers: dict[str, str]) -> _FakeResponse:
            _ = headers
            raise AssertionError("Should not poll")

    monkeypatch.setattr("esprit.runtime.cloud_runtime.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("esprit.runtime.cloud_runtime.upload_local_sources", fake_upload)

    runtime = CloudRuntime(access_token="access-token", api_base="https://api.example.test")
    result = await runtime.create_sandbox(
        agent_id="agent-upload",
        local_sources=[{"source_path": "/tmp/src", "workspace_subdir": "src"}],
    )

    # Upload must happen before either API POST
    assert call_order[0] == "upload"
    assert state["modern_payload"]["target_type"] == "local_upload"
    uuid.UUID(state["modern_payload"]["scan_id"])
    assert result["workspace_id"] == "sbx-upload-test"


@pytest.mark.asyncio
async def test_create_sandbox_skips_upload_for_no_local_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """upload_local_sources must NOT be called when no local_sources provided."""
    upload_called = False

    def fake_upload(**kwargs: Any) -> str:
        nonlocal upload_called
        upload_called = True
        return "key"

    class FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            _ = (args, kwargs)

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def post(self, url: str, *, json: dict[str, Any], headers: dict[str, str]) -> _FakeResponse:
            _ = headers
            if url.endswith("/sandbox/create"):
                return _FakeResponse(404, {"detail": "Not Found"})
            if url.endswith("/sandbox"):
                return _FakeResponse(
                    200,
                    {
                        "sandbox_id": "sbx-no-upload",
                        "status": "running",
                        "tool_server_url": "https://tool.example.test",
                    },
                )
            raise AssertionError(f"Unexpected POST URL: {url}")

        async def get(self, _url: str, *, headers: dict[str, str]) -> _FakeResponse:
            _ = headers
            raise AssertionError("Should not poll")

    monkeypatch.setattr("esprit.runtime.cloud_runtime.httpx.AsyncClient", FakeAsyncClient)
    monkeypatch.setattr("esprit.runtime.cloud_runtime.upload_local_sources", fake_upload)

    runtime = CloudRuntime(access_token="access-token", api_base="https://api.example.test")
    result = await runtime.create_sandbox(agent_id="agent-no-sources")

    assert not upload_called
    assert result["workspace_id"] == "sbx-no-upload"


def test_build_modern_payload_uses_uuid_scan_id() -> None:
    """_build_modern_sandbox_payload should use the provided UUID scan_id."""
    test_uuid = str(uuid.uuid4())
    payload = CloudRuntime._build_modern_sandbox_payload(
        agent_id="agent-1",
        sources_payload=[],
        scan_id=test_uuid,
    )
    assert payload["scan_id"] == test_uuid

    # Without explicit scan_id, should still be a valid UUID
    payload2 = CloudRuntime._build_modern_sandbox_payload(
        agent_id="agent-1",
        sources_payload=[],
    )
    uuid.UUID(payload2["scan_id"])  # validates format
    assert payload2["scan_id"] != "cli-agent-1"


def test_build_modern_payload_omits_lineage_outside_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SANDBOX_ID", raising=False)
    monkeypatch.delenv("ROOT_SANDBOX_ID", raising=False)

    payload = CloudRuntime._build_modern_sandbox_payload(
        agent_id="agent-1",
        sources_payload=[],
    )

    assert "parent_sandbox_id" not in payload
    assert "root_sandbox_id" not in payload


def test_build_modern_payload_includes_lineage_inside_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SANDBOX_ID", "sandbox-parent")
    monkeypatch.setenv("ROOT_SANDBOX_ID", "sandbox-root")

    payload = CloudRuntime._build_modern_sandbox_payload(
        agent_id="agent-1",
        sources_payload=[],
    )

    assert payload["parent_sandbox_id"] == "sandbox-parent"
    assert payload["root_sandbox_id"] == "sandbox-root"
