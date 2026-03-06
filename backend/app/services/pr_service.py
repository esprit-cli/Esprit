"""Helpers for creating GitHub pull requests from scan patch artifacts."""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import boto3
import httpx
from botocore.config import Config
from botocore.exceptions import ClientError

from app.core.config import settings


class PRServiceError(RuntimeError):
    """Raised when patch-based PR creation cannot continue safely."""


@dataclass(frozen=True)
class PreparedPRBranch:
    """Result of materializing a scan patch into a git branch."""

    branch_name: str
    base_branch: str
    patch_s3_key: str
    modified_files_count: int
    commit_sha: str


@dataclass(frozen=True)
class PullRequestResult:
    """GitHub PR creation result."""

    pr_url: str
    pr_number: int
    already_existed: bool = False


def resolve_repo_full_name(target: str, target_type: str) -> str | None:
    """Return `owner/repo` for GitHub repository scan targets."""
    if target_type not in {"repository", "public_repository"}:
        return None

    normalized = target.strip().rstrip("/")
    if normalized.startswith("https://github.com/"):
        normalized = normalized.replace("https://github.com/", "", 1)
    elif normalized.startswith("http://github.com/"):
        normalized = normalized.replace("http://github.com/", "", 1)
    elif normalized.startswith("github.com/"):
        normalized = normalized.replace("github.com/", "", 1)

    if normalized.endswith(".git"):
        normalized = normalized[:-4]

    parts = [part for part in normalized.split("/") if part]
    if len(parts) < 2:
        return None
    return f"{parts[0]}/{parts[1]}"


def resolve_patch_s3_key(scan: dict, user_id: str) -> str:
    """Resolve the stored patch artifact for a scan."""
    if not settings.s3_bucket:
        raise PRServiceError("Patch storage is not configured.")

    scan_id = str(scan.get("id") or "")
    pr_metadata = scan.get("pr_metadata", {}) or {}

    candidates: list[str] = []
    for key in (
        pr_metadata.get("patch_s3_key"),
        f"patches/{user_id}/{scan_id}.patch" if scan_id else None,
        f"patches/{scan_id}.patch" if scan_id else None,
    ):
        if key and key not in candidates:
            candidates.append(str(key))

    if not candidates:
        raise PRServiceError("No patch artifact is available for this scan.")

    s3_client = _build_s3_client()
    for patch_key in candidates:
        try:
            s3_client.head_object(Bucket=settings.s3_bucket, Key=patch_key)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"404", "NoSuchKey", "NotFound"}:
                continue
            raise PRServiceError("Failed to resolve the patch artifact.") from exc
        else:
            return patch_key

    raise PRServiceError("No patch artifact is available for this scan.")


def download_patch_text(patch_s3_key: str) -> str:
    """Download the stored patch for a scan."""
    s3_client = _build_s3_client()
    try:
        response = s3_client.get_object(Bucket=settings.s3_bucket, Key=patch_s3_key)
    except ClientError as exc:
        raise PRServiceError("Failed to download the patch artifact.") from exc

    body = response.get("Body")
    if body is None:
        raise PRServiceError("Patch artifact is empty.")

    patch_text = body.read().decode("utf-8")
    if not patch_text.strip():
        raise PRServiceError("Patch artifact is empty.")
    return patch_text


async def get_repo_default_branch(repo_full_name: str, github_token: str) -> str:
    """Fetch the repo default branch from GitHub."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.get(
            f"https://api.github.com/repos/{repo_full_name}",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github.v3+json",
            },
        )

    if response.status_code != 200:
        raise PRServiceError(f"Could not determine the default branch for {repo_full_name}.")

    default_branch = response.json().get("default_branch")
    return str(default_branch or "main")


def prepare_fix_branch(
    *,
    repo_full_name: str,
    github_token: str,
    patch_text: str,
    base_branch: str,
    branch_name: str,
    scan_id: str,
    patch_s3_key: str,
) -> PreparedPRBranch:
    """Clone the target repo, apply the patch, commit it, and push a fix branch."""
    clone_url = f"https://x-access-token:{github_token}@github.com/{repo_full_name}.git"

    with tempfile.TemporaryDirectory(prefix="esprit-pr-") as temp_dir:
        repo_dir = Path(temp_dir) / "repo"
        patch_path = Path(temp_dir) / "scan.patch"
        patch_path.write_text(patch_text, encoding="utf-8")

        _run_git(
            ["clone", "--depth", "1", "--branch", base_branch, clone_url, str(repo_dir)],
            cwd=Path(temp_dir),
        )
        _run_git(["checkout", "-B", branch_name], cwd=repo_dir)

        apply_result = _run_git(
            ["apply", "--3way", "--whitespace=nowarn", str(patch_path)],
            cwd=repo_dir,
            check=False,
        )
        if apply_result.returncode != 0:
            fallback = _run_git(
                ["apply", "--whitespace=nowarn", str(patch_path)],
                cwd=repo_dir,
                check=False,
            )
            if fallback.returncode != 0:
                raise PRServiceError("Patch could not be applied cleanly to the repository.")

        status_result = _run_git(["status", "--porcelain"], cwd=repo_dir)
        modified_entries = [line for line in status_result.stdout.splitlines() if line.strip()]
        if not modified_entries:
            raise PRServiceError("Patch did not produce any file changes.")

        _run_git(["add", "-A"], cwd=repo_dir)
        _run_git(
            [
                "-c",
                "user.name=Esprit Bot",
                "-c",
                "user.email=support@esprit.dev",
                "commit",
                "-m",
                f"Apply Esprit security fixes for scan {scan_id[:8]}",
            ],
            cwd=repo_dir,
        )
        commit_sha = _run_git(["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
        _run_git(["push", "--set-upstream", "origin", branch_name], cwd=repo_dir)

    return PreparedPRBranch(
        branch_name=branch_name,
        base_branch=base_branch,
        patch_s3_key=patch_s3_key,
        modified_files_count=len(modified_entries),
        commit_sha=commit_sha,
    )


async def create_pull_request(
    *,
    repo_full_name: str,
    github_token: str,
    branch_name: str,
    base_branch: str,
    title: str,
    body: str,
) -> PullRequestResult:
    """Create a GitHub PR, or return the existing one for the same branch."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"https://api.github.com/repos/{repo_full_name}/pulls",
            headers={
                "Authorization": f"Bearer {github_token}",
                "Accept": "application/vnd.github.v3+json",
            },
            json={
                "title": title,
                "body": body,
                "head": branch_name,
                "base": base_branch,
            },
        )

        if response.status_code == 201:
            data = response.json()
            return PullRequestResult(
                pr_url=str(data["html_url"]),
                pr_number=int(data["number"]),
            )

        if response.status_code == 422:
            owner = repo_full_name.split("/", 1)[0]
            existing = await client.get(
                f"https://api.github.com/repos/{repo_full_name}/pulls",
                headers={
                    "Authorization": f"Bearer {github_token}",
                    "Accept": "application/vnd.github.v3+json",
                },
                params={"head": f"{owner}:{branch_name}", "state": "open"},
            )
            if existing.status_code == 200:
                pulls = existing.json()
                if pulls:
                    first = pulls[0]
                    return PullRequestResult(
                        pr_url=str(first["html_url"]),
                        pr_number=int(first["number"]),
                        already_existed=True,
                    )

            try:
                payload = response.json()
            except ValueError:
                payload = {}
            error_msg = str(payload.get("message") or "GitHub rejected the pull request.")
            raise PRServiceError(error_msg)

        raise PRServiceError(f"GitHub API error: {response.status_code}")


def _build_s3_client():  # type: ignore[no-untyped-def]
    return boto3.client(
        "s3",
        region_name=settings.aws_region,
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        config=Config(signature_version="s3v4"),
    )


def _run_git(
    args: list[str],
    *,
    cwd: Path,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        capture_output=True,
        check=False,
    )
    if check and completed.returncode != 0:
        raise PRServiceError(completed.stderr.strip() or completed.stdout.strip() or "git command failed")
    return completed
