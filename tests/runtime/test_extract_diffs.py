"""Tests for esprit.runtime.extract_and_save_diffs.

Covers patch generation for all three edit commands (str_replace, create, insert)
and verifies the JSON + patch files written to the run directory.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


def _make_tracer(save_dir: Path) -> MagicMock:
    tracer = MagicMock()
    tracer.save_dir = str(save_dir)
    return tracer


def _call_extract(edits: list[dict[str, Any]], save_dir: Path) -> list[dict[str, Any]]:
    """Invoke extract_and_save_diffs with a fake runtime returning *edits*."""
    import esprit.runtime as rt_module

    fake_runtime = MagicMock()
    fake_runtime.get_workspace_diffs = MagicMock(return_value=edits)

    tracer = _make_tracer(save_dir)

    with (
        patch.object(rt_module, "_global_runtime", fake_runtime),
        patch("esprit.telemetry.tracer.get_global_tracer", return_value=tracer),
        # Prevent S3 upload attempts
        patch.object(rt_module, "_upload_patch_to_s3"),
        # Run sync so asyncio.run() path is taken
        patch("asyncio.get_running_loop", side_effect=RuntimeError),
        patch("asyncio.run", side_effect=lambda coro: edits),
    ):
        return rt_module.extract_and_save_diffs("sandbox-123")


class TestExtractAndSaveDiffs:
    def test_returns_empty_when_no_runtime(self, tmp_path: Path) -> None:
        import esprit.runtime as rt_module

        with patch.object(rt_module, "_global_runtime", None):
            result = rt_module.extract_and_save_diffs("sandbox-1")

        assert result == []

    def test_returns_empty_when_no_sandbox_id(self, tmp_path: Path) -> None:
        import esprit.runtime as rt_module

        fake_runtime = MagicMock()
        with patch.object(rt_module, "_global_runtime", fake_runtime):
            result = rt_module.extract_and_save_diffs("")

        assert result == []

    def test_str_replace_written_to_patch(self, tmp_path: Path) -> None:
        edits = [
            {
                "command": "str_replace",
                "path": "/workspace/app.py",
                "old_str": "x = 1",
                "new_str": "x = 2",
            }
        ]
        result = _call_extract(edits, tmp_path)
        assert result == edits

        patch_file = tmp_path / "patches" / "remediation.patch"
        assert patch_file.exists()
        content = patch_file.read_text()
        assert "--- a/workspace/app.py" in content
        assert "+++ b/workspace/app.py" in content
        assert "-x = 1" in content
        assert "+x = 2" in content

    def test_create_written_to_patch(self, tmp_path: Path) -> None:
        edits = [
            {
                "command": "create",
                "path": "/workspace/new_file.py",
                "file_text": "print('hello')\n",
            }
        ]
        result = _call_extract(edits, tmp_path)

        patch_file = tmp_path / "patches" / "remediation.patch"
        assert patch_file.exists()
        content = patch_file.read_text()
        assert "--- /dev/null" in content
        assert "+++ b/workspace/new_file.py" in content
        assert "+print('hello')" in content

    def test_insert_written_to_patch(self, tmp_path: Path) -> None:
        """insert command must appear in the patch — regression test for the bug fix."""
        edits = [
            {
                "command": "insert",
                "path": "/workspace/config.py",
                "new_str": "DEBUG = True",
                "insert_line": 5,
            }
        ]
        result = _call_extract(edits, tmp_path)

        patch_file = tmp_path / "patches" / "remediation.patch"
        assert patch_file.exists()
        content = patch_file.read_text()
        assert "--- a/workspace/config.py" in content
        assert "+++ b/workspace/config.py" in content
        assert "@@ -5,0 +5,1 @@" in content
        assert "+DEBUG = True" in content

    def test_edits_json_written(self, tmp_path: Path) -> None:
        import json

        edits = [{"command": "str_replace", "path": "/workspace/x.py", "old_str": "a", "new_str": "b"}]
        _call_extract(edits, tmp_path)

        edits_file = tmp_path / "patches" / "edits.json"
        assert edits_file.exists()
        loaded = json.loads(edits_file.read_text())
        assert loaded == edits

    def test_no_patch_file_when_no_mutating_edits(self, tmp_path: Path) -> None:
        """view-only edits (no str_replace/create/insert) produce no patch file."""
        edits = [{"command": "view", "path": "/workspace/app.py"}]
        _call_extract(edits, tmp_path)

        patch_file = tmp_path / "patches" / "remediation.patch"
        assert not patch_file.exists()

    def test_multiple_commands_all_appear_in_patch(self, tmp_path: Path) -> None:
        edits = [
            {"command": "str_replace", "path": "/workspace/a.py", "old_str": "old", "new_str": "new"},
            {"command": "create", "path": "/workspace/b.py", "file_text": "# b"},
            {"command": "insert", "path": "/workspace/c.py", "new_str": "# inserted", "insert_line": 1},
        ]
        _call_extract(edits, tmp_path)

        content = (tmp_path / "patches" / "remediation.patch").read_text()
        assert "/workspace/a.py" in content
        assert "/workspace/b.py" in content
        assert "/workspace/c.py" in content
        assert "+# b" in content
        assert "+# inserted" in content
