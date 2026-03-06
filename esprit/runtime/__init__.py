import os

from esprit.config import Config

from .runtime import AbstractRuntime


class SandboxInitializationError(Exception):
    """Raised when sandbox initialization fails (e.g., Docker issues)."""

    def __init__(self, message: str, details: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details


_global_runtime: AbstractRuntime | None = None


def get_runtime() -> AbstractRuntime:
    global _global_runtime  # noqa: PLW0603

    runtime_backend = Config.get("esprit_runtime_backend")

    if runtime_backend == "cloud":
        from esprit.auth.credentials import get_auth_token

        from .cloud_runtime import CloudRuntime

        auth_token = get_auth_token()
        if not auth_token:
            raise SandboxInitializationError(
                "Esprit Cloud authentication required.",
                "Run `esprit login` and try again.",
            )

        api_base = os.getenv("ESPRIT_API_URL", "https://esprit.dev/api/v1")
        if _global_runtime is None or not isinstance(_global_runtime, CloudRuntime):
            _global_runtime = CloudRuntime(access_token=auth_token, api_base=api_base)
        return _global_runtime

    if runtime_backend == "docker":
        from .docker_runtime import DockerRuntime

        if _global_runtime is None or not isinstance(_global_runtime, DockerRuntime):
            _global_runtime = DockerRuntime()
        return _global_runtime

    raise ValueError(f"Unsupported runtime backend: {runtime_backend}.")


def _diff_source_ids(primary_sandbox_id: str) -> list[str]:
    if _global_runtime is None or not primary_sandbox_id:
        return []

    get_diff_source_ids = getattr(_global_runtime, "get_diff_source_ids", None)
    if callable(get_diff_source_ids):
        candidates = get_diff_source_ids(primary_sandbox_id)
        if isinstance(candidates, list):
            ordered: list[str] = []
            seen: set[str] = set()
            for item in candidates:
                sandbox_id = str(item or "").strip()
                if not sandbox_id or sandbox_id in seen:
                    continue
                seen.add(sandbox_id)
                ordered.append(sandbox_id)
            if ordered:
                return ordered
    return [primary_sandbox_id]


def _fetch_workspace_diffs(sandbox_id: str) -> list[dict[str, object]]:
    import asyncio

    if _global_runtime is None or not sandbox_id:
        return []

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(
                asyncio.run, _global_runtime.get_workspace_diffs(sandbox_id)
            ).result(timeout=15)

    return asyncio.run(_global_runtime.get_workspace_diffs(sandbox_id))


def extract_and_save_diffs(sandbox_id: str) -> list[dict[str, object]]:
    """Pull file edits from one or more sandboxes and persist them to the run directory.

    Call this BEFORE cleanup_runtime() while the sandbox is still alive.
    Returns the list of edit records for further processing.
    """
    import json
    import logging

    log = logging.getLogger(__name__)

    if _global_runtime is None or not sandbox_id:
        return []

    edits: list[dict[str, object]] = []
    diff_source_ids = _diff_source_ids(sandbox_id)
    for diff_source_id in diff_source_ids:
        try:
            sandbox_edits = _fetch_workspace_diffs(diff_source_id)
        except Exception:  # noqa: BLE001
            log.exception("Failed to extract workspace diffs from sandbox %s", diff_source_id)
            continue

        for edit in sandbox_edits:
            tagged_edit = dict(edit)
            tagged_edit.setdefault("sandbox_id", diff_source_id)
            edits.append(tagged_edit)

    if not edits:
        return []

    log.info(
        "Extracted %d file edits from %d sandbox(es)",
        len(edits),
        len(diff_source_ids),
    )

    # Persist to the tracer's run directory if available
    try:
        from esprit.telemetry.tracer import get_global_tracer

        tracer = get_global_tracer()
        if tracer and hasattr(tracer, "save_dir") and tracer.save_dir:
            from pathlib import Path

            patches_dir = Path(tracer.save_dir) / "patches"
            patches_dir.mkdir(parents=True, exist_ok=True)

            # Write machine-readable JSON
            (patches_dir / "edits.json").write_text(
                json.dumps(edits, indent=2, default=str)
            )

            # Write human-readable unified diff summary
            lines: list[str] = []
            for edit in edits:
                path = edit.get("path", "unknown")
                cmd = edit.get("command", "?")
                if cmd == "str_replace":
                    lines.append(f"--- a{path}")
                    lines.append(f"+++ b{path}")
                    for old_line in str(edit.get("old_str", "")).splitlines():
                        lines.append(f"-{old_line}")
                    for new_line in str(edit.get("new_str", "")).splitlines():
                        lines.append(f"+{new_line}")
                    lines.append("")
                elif cmd == "create":
                    lines.append(f"--- /dev/null")
                    lines.append(f"+++ b{path}")
                    for new_line in str(edit.get("file_text", "")).splitlines():
                        lines.append(f"+{new_line}")
                    lines.append("")
                elif cmd == "insert":
                    lines.append(f"--- a{path}")
                    lines.append(f"+++ b{path}")
                    new_lines = str(edit.get("new_str", "")).splitlines()
                    insert_line = edit.get("insert_line")
                    if isinstance(insert_line, int) and insert_line > 0:
                        lines.append(
                            f"@@ -{insert_line},0 +{insert_line},{len(new_lines)} @@"
                        )
                    for new_line in new_lines:
                        lines.append(f"+{new_line}")
                    lines.append("")
            if lines:
                patch_content = "\n".join(lines)
                (patches_dir / "remediation.patch").write_text(patch_content)
                log.info("Saved patches to %s", patches_dir)

                # Upload to S3 for hosted mode (backend serves via /scans/{id}/patch)
                _upload_patch_to_s3(patch_content, log)
    except Exception:  # noqa: BLE001
        log.debug("Could not persist diffs to run directory", exc_info=True)

    return edits


def _upload_patch_to_s3(patch_content: str, log: "logging.Logger") -> None:
    """Upload patch to S3 if running in hosted mode."""
    import os

    bucket = os.getenv("S3_BUCKET")
    patch_s3_key = (os.getenv("PATCH_S3_KEY") or "").strip()
    scan_id = os.getenv("SCAN_ID")
    if not bucket:
        return

    try:
        import boto3

        s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION", "us-east-1"))
        s3_key = patch_s3_key or (f"patches/{scan_id}.patch" if scan_id else "")
        if not s3_key:
            return
        s3.put_object(Bucket=bucket, Key=s3_key, Body=patch_content.encode("utf-8"))
        log.info("Uploaded patch to s3://%s/%s", bucket, s3_key)
    except Exception:  # noqa: BLE001
        log.debug("S3 patch upload failed (non-fatal)", exc_info=True)


def cleanup_runtime() -> None:
    global _global_runtime  # noqa: PLW0603

    if _global_runtime is not None:
        _global_runtime.cleanup()
        _global_runtime = None


__all__ = [
    "AbstractRuntime",
    "SandboxInitializationError",
    "cleanup_runtime",
    "extract_and_save_diffs",
    "get_runtime",
]
