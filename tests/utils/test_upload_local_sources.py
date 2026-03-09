"""Tests for upload_local_sources function."""

from __future__ import annotations

import importlib
import importlib.util
import sys
import tarfile
from io import BytesIO
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

# Add cli/ to path so we can import strix.interface.utils directly
_cli_path = str(Path(__file__).resolve().parents[2] / "cli")
if _cli_path not in sys.path:
    sys.path.insert(0, _cli_path)

# Import the utils module directly by file path to avoid triggering
# strix.interface.__init__.py's heavy import chain (litellm, docker, etc.)
_utils_spec = importlib.util.spec_from_file_location(
    "_strix_utils",
    Path(__file__).resolve().parents[2] / "cli" / "strix" / "interface" / "utils.py",
)
_utils_mod = importlib.util.module_from_spec(_utils_spec)  # type: ignore[arg-type]
_utils_spec.loader.exec_module(_utils_mod)  # type: ignore[union-attr]
upload_local_sources = _utils_mod.upload_local_sources


def _make_mock_client(
    post_return: dict[str, Any] | None = None,
    captured: dict[str, bytes] | None = None,
    post_side_effect: Exception | None = None,
) -> MagicMock:
    """Build a mock httpx.Client context manager."""
    if post_return is None:
        post_return = {
            "upload_url": "https://s3.example.com/presigned-put",
            "s3_key": "uploads/user-1/scan-1.tar.gz",
        }

    def mock_post(*args: Any, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        if post_side_effect:
            resp.raise_for_status.side_effect = post_side_effect
        else:
            resp.raise_for_status.return_value = None
        resp.json.return_value = post_return
        return resp

    def mock_put(*args: Any, **kwargs: Any) -> MagicMock:
        if captured is not None:
            captured["body"] = kwargs.get("content", b"")
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        return resp

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post = mock_post
    mock_client.put = mock_put
    return mock_client


def test_upload_local_sources_creates_valid_tar_gz(tmp_path: Path) -> None:
    """upload_local_sources should create a tar.gz with correct workspace_subdir paths."""
    src_dir = tmp_path / "my-project"
    src_dir.mkdir()
    (src_dir / "main.py").write_text("print('hello')")
    (src_dir / "lib").mkdir()
    (src_dir / "lib" / "utils.py").write_text("# utils")

    local_sources = [{"source_path": str(src_dir), "workspace_subdir": "my-project"}]
    captured: dict[str, bytes] = {}

    with patch("httpx.Client", return_value=_make_mock_client(captured=captured)):
        s3_key = upload_local_sources(
            local_sources, "scan-1", "https://api.example.com/v1", "token-1"
        )

    assert s3_key == "uploads/user-1/scan-1.tar.gz"
    tar_bytes = captured["body"]
    assert len(tar_bytes) > 0
    with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:gz") as tar:
        names = sorted(tar.getnames())
        assert "my-project/main.py" in names
        assert "my-project/lib/utils.py" in names


def test_upload_local_sources_skips_nonexistent_paths() -> None:
    """upload_local_sources should not error on missing source paths."""
    local_sources = [{"source_path": "/nonexistent/path/abc", "workspace_subdir": "abc"}]
    captured: dict[str, bytes] = {}

    with patch("httpx.Client", return_value=_make_mock_client(captured=captured)):
        s3_key = upload_local_sources(
            local_sources, "scan-1", "https://api.example.com/v1", "token-1"
        )

    assert s3_key == "uploads/user-1/scan-1.tar.gz"
    tar_bytes = captured["body"]
    with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:gz") as tar:
        assert tar.getnames() == []


def test_upload_local_sources_presigned_url_failure_raises() -> None:
    """upload_local_sources should propagate HTTP errors from presigned URL request."""
    local_sources = [{"source_path": "/nonexistent", "workspace_subdir": "test"}]

    request = httpx.Request("POST", "https://api.example.com/v1/uploads/presigned-url")
    response = httpx.Response(403, request=request, text="forbidden")
    err = httpx.HTTPStatusError("forbidden", request=request, response=response)

    with patch("httpx.Client", return_value=_make_mock_client(post_side_effect=err)):
        with pytest.raises(httpx.HTTPStatusError):
            upload_local_sources(
                local_sources, "scan-1", "https://api.example.com/v1", "token-1"
            )


def test_upload_local_sources_multiple_sources(tmp_path: Path) -> None:
    """upload_local_sources should combine multiple source dirs into one tar.gz."""
    dir1 = tmp_path / "frontend"
    dir1.mkdir()
    (dir1 / "index.html").write_text("<html>")

    dir2 = tmp_path / "backend"
    dir2.mkdir()
    (dir2 / "app.py").write_text("# app")

    local_sources = [
        {"source_path": str(dir1), "workspace_subdir": "frontend"},
        {"source_path": str(dir2), "workspace_subdir": "backend"},
    ]
    captured: dict[str, bytes] = {}

    with patch("httpx.Client", return_value=_make_mock_client(captured=captured)):
        upload_local_sources(
            local_sources, "scan-1", "https://api.example.com/v1", "token-1"
        )

    tar_bytes = captured["body"]
    with tarfile.open(fileobj=BytesIO(tar_bytes), mode="r:gz") as tar:
        names = sorted(tar.getnames())
        assert "frontend/index.html" in names
        assert "backend/app.py" in names
