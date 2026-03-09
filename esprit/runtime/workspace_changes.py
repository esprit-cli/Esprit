from __future__ import annotations

import difflib
import hashlib
import os
import shutil
from pathlib import Path
from typing import Any

EXCLUDE_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".env",
    "env",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    ".coverage",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".turbo",
    "target",
    "vendor",
    ".bundle",
}

_WORKSPACE_ROOT = Path("/workspace")
_BASELINE_ENV = "ESPRIT_WORKSPACE_BASELINE"
_DEFAULT_BASELINE_ROOT = Path("/tmp/esprit-workspace-baseline")


def _baseline_root() -> Path:
    override = (os.getenv(_BASELINE_ENV) or "").strip()
    if override:
        return Path(override)
    return _DEFAULT_BASELINE_ROOT


def _iter_files(root: Path):
    if not root.exists():
        return
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_path = path.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel_path.parts):
            continue
        yield rel_path, path


def create_workspace_baseline(
    workspace_root: Path | None = None,
    baseline_root: Path | None = None,
) -> Path:
    workspace_root = workspace_root or _WORKSPACE_ROOT
    baseline_root = baseline_root or _baseline_root()

    if baseline_root.exists():
        shutil.rmtree(baseline_root)
    baseline_root.mkdir(parents=True, exist_ok=True)

    if not workspace_root.exists():
        return baseline_root

    for rel_path, src_path in _iter_files(workspace_root):
        dest_path = baseline_root / rel_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src_path, dest_path)

    return baseline_root


def _read_file(path: Path) -> tuple[str | None, bool, bytes]:
    data = path.read_bytes()
    try:
        return data.decode("utf-8"), False, data
    except UnicodeDecodeError:
        return None, True, data


def _sha256_text(text: str | None) -> str | None:
    if text is None:
        return None
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes | None) -> str | None:
    if data is None:
        return None
    return hashlib.sha256(data).hexdigest()


def _common_prefix(rel_paths: list[Path]) -> str | None:
    top_parts = {rel.parts[0] for rel in rel_paths if rel.parts}
    if len(top_parts) != 1:
        return None
    return next(iter(top_parts))


def _normalize_rel_path(rel_path: Path, prefix: str | None) -> str:
    if prefix and rel_path.parts and rel_path.parts[0] == prefix:
        trimmed = Path(*rel_path.parts[1:])
        if str(trimmed):
            return str(trimmed)
    return str(rel_path)


def collect_workspace_changes(
    workspace_root: Path | None = None,
    baseline_root: Path | None = None,
) -> dict[str, Any]:
    workspace_root = workspace_root or _WORKSPACE_ROOT
    baseline_root = baseline_root or _baseline_root()

    workspace_files = {rel: path for rel, path in _iter_files(workspace_root)}
    baseline_files = {rel: path for rel, path in _iter_files(baseline_root)}
    all_rel_paths = sorted(set(workspace_files) | set(baseline_files))
    baseline_prefix = _common_prefix(list(baseline_files))
    prefix = baseline_prefix or _common_prefix(all_rel_paths)

    changes: list[dict[str, Any]] = []
    patch_fragments: list[str] = []

    for rel_path in all_rel_paths:
        before_path = baseline_files.get(rel_path)
        after_path = workspace_files.get(rel_path)

        if (
            baseline_prefix
            and before_path is None
            and rel_path.parts
            and rel_path.parts[0] != baseline_prefix
        ):
            continue

        normalized_path = _normalize_rel_path(rel_path, prefix)

        before_text = None
        after_text = None
        before_binary = False
        after_binary = False
        before_data = None
        after_data = None

        if before_path:
            before_text, before_binary, before_data = _read_file(before_path)
        if after_path:
            after_text, after_binary, after_data = _read_file(after_path)

        if before_path and after_path:
            if before_binary or after_binary:
                if before_data == after_data:
                    continue
            elif before_text == after_text:
                continue

        if before_path and after_path:
            status = "modified"
            fromfile = f"a/{normalized_path}"
            tofile = f"b/{normalized_path}"
        elif after_path:
            status = "created"
            fromfile = "/dev/null"
            tofile = f"b/{normalized_path}"
        else:
            status = "deleted"
            fromfile = f"a/{normalized_path}"
            tofile = "/dev/null"

        patch_text = ""
        if not before_binary and not after_binary:
            patch_lines = list(
                difflib.unified_diff(
                    [] if before_text is None else before_text.splitlines(),
                    [] if after_text is None else after_text.splitlines(),
                    fromfile=fromfile,
                    tofile=tofile,
                    lineterm="",
                    n=3,
                )
            )
            if patch_lines:
                patch_text = "\n".join(patch_lines) + "\n"
                patch_fragments.append(patch_text)

        changes.append(
            {
                "path": normalized_path,
                "workspace_path": str(rel_path),
                "status": status,
                "binary": before_binary or after_binary,
                "before_sha256": _sha256_bytes(before_data)
                if before_binary
                else _sha256_text(before_text),
                "after_sha256": _sha256_bytes(after_data)
                if after_binary
                else _sha256_text(after_text),
                "patch": patch_text,
            }
        )

    return {
        "workspace_root": str(workspace_root),
        "baseline_root": str(baseline_root),
        "path_prefix": prefix,
        "count": len(changes),
        "changes": changes,
        "patch": "".join(patch_fragments),
    }
