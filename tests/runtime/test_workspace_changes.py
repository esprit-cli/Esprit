from __future__ import annotations

from pathlib import Path

from esprit.runtime.workspace_changes import collect_workspace_changes, create_workspace_baseline


def test_collect_workspace_changes_uses_repo_relative_paths(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    baseline = tmp_path / 'baseline'
    repo = workspace / 'repo'
    repo.mkdir(parents=True)
    target = repo / 'app.py'
    target.write_text("debug = True\n")

    create_workspace_baseline(workspace, baseline)

    target.write_text("debug = False\n")
    changes = collect_workspace_changes(workspace, baseline)

    assert changes['path_prefix'] == 'repo'
    assert changes['count'] == 1
    assert changes['changes'][0]['path'] == 'app.py'
    assert changes['changes'][0]['status'] == 'modified'
    assert '--- a/app.py' in changes['patch']
    assert '+++ b/app.py' in changes['patch']
    assert '@@' in changes['patch']


def test_collect_workspace_changes_detects_created_and_deleted_files(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    baseline = tmp_path / 'baseline'
    repo = workspace / 'repo'
    repo.mkdir(parents=True)
    keep = repo / 'keep.py'
    old = repo / 'old.py'
    keep.write_text("print('keep')\n")
    old.write_text("print('old')\n")

    create_workspace_baseline(workspace, baseline)

    old.unlink()
    (repo / 'new.py').write_text("print('new')\n")

    changes = collect_workspace_changes(workspace, baseline)

    statuses = {change['path']: change['status'] for change in changes['changes']}
    assert statuses['old.py'] == 'deleted'
    assert statuses['new.py'] == 'created'
    assert '--- /dev/null' in changes['patch']
    assert '+++ b/new.py' in changes['patch']
    assert '--- a/old.py' in changes['patch']
    assert '+++ /dev/null' in changes['patch']


def test_collect_workspace_changes_ignores_unchanged_binary_files(tmp_path: Path) -> None:
    workspace = tmp_path / 'workspace'
    baseline = tmp_path / 'baseline'
    repo = workspace / 'repo'
    repo.mkdir(parents=True)
    asset = repo / 'logo.png'
    asset.write_bytes(b'\x89PNG\r\n\x1a\nbinary-data')

    create_workspace_baseline(workspace, baseline)

    changes = collect_workspace_changes(workspace, baseline)

    assert changes['count'] == 0
    assert changes['changes'] == []
    assert changes['patch'] == ''


def test_collect_workspace_changes_ignores_created_files_outside_single_source_root(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / 'workspace'
    baseline = tmp_path / 'baseline'
    repo = workspace / 'repo'
    repo.mkdir(parents=True)
    target = repo / 'app.py'
    target.write_text("debug = True\n")

    create_workspace_baseline(workspace, baseline)

    target.write_text("debug = False\n")
    (workspace / 'AUTH_BYPASS_FIX_SUMMARY.md').write_text('generated notes\n')

    changes = collect_workspace_changes(workspace, baseline)

    assert changes['path_prefix'] == 'repo'
    assert changes['count'] == 1
    assert changes['changes'][0]['path'] == 'app.py'
    assert 'AUTH_BYPASS_FIX_SUMMARY.md' not in changes['patch']
