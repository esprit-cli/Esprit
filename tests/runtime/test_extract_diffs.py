"""Tests for runtime patch upload key selection."""

from __future__ import annotations

from unittest.mock import MagicMock


class _FakeS3Client:
    def __init__(self) -> None:
        self.put_calls: list[dict[str, object]] = []

    def put_object(self, **kwargs):  # type: ignore[no-untyped-def]
        self.put_calls.append(kwargs)
        return {"ETag": "fake"}


def test_upload_patch_to_s3_prefers_explicit_patch_key(monkeypatch) -> None:
    import esprit.runtime as runtime_module

    fake_s3 = _FakeS3Client()
    monkeypatch.setenv("S3_BUCKET", "scan-artifacts")
    monkeypatch.setenv("SCAN_ID", "scan-123")
    monkeypatch.setenv("PATCH_S3_KEY", "patches/user-1/scan-123.patch")
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: fake_s3)

    runtime_module._upload_patch_to_s3("diff-content", MagicMock())

    assert len(fake_s3.put_calls) == 1
    assert fake_s3.put_calls[0]["Bucket"] == "scan-artifacts"
    assert fake_s3.put_calls[0]["Key"] == "patches/user-1/scan-123.patch"


def test_upload_patch_to_s3_falls_back_to_legacy_key(monkeypatch) -> None:
    import esprit.runtime as runtime_module

    fake_s3 = _FakeS3Client()
    monkeypatch.setenv("S3_BUCKET", "scan-artifacts")
    monkeypatch.setenv("SCAN_ID", "scan-legacy")
    monkeypatch.delenv("PATCH_S3_KEY", raising=False)
    monkeypatch.setattr("boto3.client", lambda *_args, **_kwargs: fake_s3)

    runtime_module._upload_patch_to_s3("diff-content", MagicMock())

    assert len(fake_s3.put_calls) == 1
    assert fake_s3.put_calls[0]["Key"] == "patches/scan-legacy.patch"
