from pathlib import Path

from esprit.run_history import (
    append_run_registry_entry,
    build_resume_instruction,
    build_run_manifest,
    list_runs,
    write_run_manifest,
)


def test_list_runs_merges_local_and_global(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("esprit.run_history.Config.config_dir", lambda: tmp_path / ".esprit")

    run_dir = tmp_path / "esprit_runs" / "demo-run"
    manifest = build_run_manifest(
        run_dir=run_dir,
        run_metadata={
            "run_id": "demo-run",
            "run_name": "demo-run",
            "start_time": "2026-04-08T10:00:00+00:00",
            "end_time": None,
            "status": "running",
        },
        scan_config={
            "targets": [{"type": "web_application", "details": {"target_url": "https://example.com"}, "original": "https://example.com"}],
            "cwd": str(tmp_path),
            "scan_mode": "deep",
            "model": "openai/gpt-5",
            "user_instructions": "focus on auth",
            "local_sources": [],
        },
        llm_stats={"total": {"cost": 0.12}, "total_tokens": 1200, "max_context_tokens": 400},
        vulnerability_reports=[{"severity": "high"}],
        final_report_present=False,
    )
    write_run_manifest(run_dir, manifest)
    append_run_registry_entry(manifest)

    runs = list_runs(cwd=tmp_path, scope="all")

    assert len(runs) == 1
    assert runs[0]["run_name"] == "demo-run"
    assert runs[0]["target_summary"] == "https://example.com"
    assert runs[0]["findings_total"] == 1
    assert runs[0]["resume"]["resumable"] is True


def test_list_runs_supports_legacy_runs(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr("esprit.run_history.Config.config_dir", lambda: tmp_path / ".esprit")

    run_dir = tmp_path / "esprit_runs" / "legacy-run"
    run_dir.mkdir(parents=True)
    (run_dir / "vulnerabilities.csv").write_text("id,title\nv1,test\n", encoding="utf-8")

    runs = list_runs(cwd=tmp_path, scope="cwd")

    assert len(runs) == 1
    assert runs[0]["run_name"] == "legacy-run"
    assert runs[0]["findings_total"] == 1
    assert runs[0]["resume"]["resumable"] is False


def test_build_resume_instruction_mentions_findings_and_report() -> None:
    instruction = build_resume_instruction(
        {
            "run_name": "demo-run",
            "status": "failed",
            "run_id": "demo-run",
            "findings_total": 2,
            "findings_by_severity": {"high": 1, "medium": 1},
            "artifacts": {"report_available": True, "report_path": "/tmp/report.md"},
        }
    )

    assert "demo-run" in instruction
    assert "Previous findings: 2" in instruction
    assert "/tmp/report.md" in instruction
