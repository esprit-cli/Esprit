"""Tests for patch-driven PR service helpers."""

from __future__ import annotations

from io import BytesIO

from botocore.exceptions import ClientError

from app.services import pr_service


class _FakeS3Client:
    def __init__(self, existing_keys: set[str], payloads: dict[str, bytes] | None = None) -> None:
        self.existing_keys = existing_keys
        self.payloads = payloads or {}

    def head_object(self, **kwargs):  # type: ignore[no-untyped-def]
        key = kwargs["Key"]
        if key not in self.existing_keys:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, **kwargs):  # type: ignore[no-untyped-def]
        key = kwargs["Key"]
        return {"Body": BytesIO(self.payloads[key])}


def test_resolve_repo_full_name_normalizes_github_targets() -> None:
    assert pr_service.resolve_repo_full_name("https://github.com/acme/api", "repository") == "acme/api"
    assert pr_service.resolve_repo_full_name("github.com/acme/api.git", "public_repository") == "acme/api"
    assert pr_service.resolve_repo_full_name("acme/api", "repository") == "acme/api"
    assert pr_service.resolve_repo_full_name("https://example.com", "url") is None


def test_resolve_patch_s3_key_prefers_metadata_key(monkeypatch) -> None:
    scan_id = "00000000-0000-0000-0000-000000000111"
    scan = {
        "id": scan_id,
        "pr_metadata": {"patch_s3_key": f"patches/custom/{scan_id}.patch"},
    }

    monkeypatch.setattr(pr_service.settings, "s3_bucket", "scan-artifacts")
    monkeypatch.setattr(
        pr_service,
        "_build_s3_client",
        lambda: _FakeS3Client({f"patches/custom/{scan_id}.patch", f"patches/user-1/{scan_id}.patch"}),
    )

    assert pr_service.resolve_patch_s3_key(scan, "user-1") == f"patches/custom/{scan_id}.patch"


def test_download_patch_text_reads_s3_payload(monkeypatch) -> None:
    patch_key = "patches/user-1/scan.patch"
    monkeypatch.setattr(pr_service.settings, "s3_bucket", "scan-artifacts")
    monkeypatch.setattr(
        pr_service,
        "_build_s3_client",
        lambda: _FakeS3Client({patch_key}, {patch_key: b"--- a.py\n+++ a.py\n@@ -1 +1 @@\n-hi\n+bye\n"}),
    )

    assert "+bye" in pr_service.download_patch_text(patch_key)
