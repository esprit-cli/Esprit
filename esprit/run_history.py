from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from esprit.config import Config


RUN_MANIFEST_FILENAME = "run.json"
GLOBAL_RUNS_DIRNAME = "runs"
GLOBAL_RUN_INDEX_FILENAME = "index.jsonl"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def get_local_runs_dir(cwd: Path | None = None) -> Path:
    return (cwd or Path.cwd()) / "esprit_runs"


def get_global_runs_dir() -> Path:
    return Config.config_dir() / GLOBAL_RUNS_DIRNAME


def get_global_run_index_path() -> Path:
    return get_global_runs_dir() / GLOBAL_RUN_INDEX_FILENAME


def get_run_manifest_path(run_dir: Path) -> Path:
    return run_dir / RUN_MANIFEST_FILENAME


def _count_vulnerabilities_from_csv(csv_path: Path) -> int:
    if not csv_path.exists():
        return 0
    try:
        lines = csv_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    return max(0, len(lines) - 1)


def _derive_target_summary(targets: list[dict[str, Any]] | None, run_name: str) -> str:
    if not targets:
        return run_name
    if len(targets) == 1:
        target = targets[0] or {}
        original = str(target.get("original") or "").strip()
        if original:
            return original
        details = target.get("details", {}) or {}
        for key in ("target_url", "target_repo", "target_path", "target_ip"):
            value = str(details.get(key) or "").strip()
            if value:
                return value
    return f"{len(targets)} targets"


def _count_findings_by_severity(vulnerability_reports: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}
    for report in vulnerability_reports:
        severity = str(report.get("severity", "")).lower()
        if severity in counts:
            counts[severity] += 1
    return counts


def _artifact_metadata(run_dir: Path, findings_total: int, final_report_present: bool) -> dict[str, Any]:
    report_path = run_dir / "penetration_test_report.md"
    vuln_csv_path = run_dir / "vulnerabilities.csv"
    vuln_dir = run_dir / "vulnerabilities"
    patches_dir = run_dir / "patches"
    return {
        "report_available": final_report_present or report_path.exists(),
        "report_path": str(report_path),
        "vulnerabilities_csv_path": str(vuln_csv_path),
        "vulnerabilities_dir": str(vuln_dir),
        "patches_dir": str(patches_dir),
        "patches_available": patches_dir.exists(),
        "findings_total": findings_total,
    }


def _resume_metadata(
    targets: list[dict[str, Any]] | None,
    local_sources: list[dict[str, str]] | None,
) -> dict[str, Any]:
    missing_paths: list[str] = []

    for target in targets or []:
        target_type = str(target.get("type") or "")
        details = target.get("details", {}) or {}
        if target_type == "local_code":
            target_path = str(details.get("target_path") or "").strip()
            if target_path and not Path(target_path).exists():
                missing_paths.append(target_path)

    resumable = len(missing_paths) == 0 and bool(targets)
    return {
        "resumable": resumable,
        "missing_paths": missing_paths,
        "local_sources": list(local_sources or []),
    }


def build_run_manifest(
    *,
    run_dir: Path,
    run_metadata: dict[str, Any],
    scan_config: dict[str, Any] | None,
    llm_stats: dict[str, Any],
    vulnerability_reports: list[dict[str, Any]],
    final_report_present: bool,
) -> dict[str, Any]:
    scan_config = scan_config or {}
    targets = list(scan_config.get("targets", []) or [])
    local_sources = list(scan_config.get("local_sources", []) or [])
    llm_total = dict(llm_stats.get("total", {}) or {})
    findings_by_severity = _count_findings_by_severity(vulnerability_reports)
    findings_total = len(vulnerability_reports)

    manifest = {
        "manifest_version": 1,
        "updated_at": _now_iso(),
        "run_id": str(run_metadata.get("run_id") or run_metadata.get("run_name") or run_dir.name),
        "run_name": str(run_metadata.get("run_name") or run_dir.name),
        "run_dir": str(run_dir),
        "cwd": str(scan_config.get("cwd") or run_metadata.get("cwd") or ""),
        "status": str(run_metadata.get("status") or "running"),
        "start_time": run_metadata.get("start_time"),
        "end_time": run_metadata.get("end_time"),
        "scan_mode": str(scan_config.get("scan_mode") or run_metadata.get("scan_mode") or "deep"),
        "model": str(scan_config.get("model") or run_metadata.get("model") or ""),
        "targets": targets,
        "target_summary": _derive_target_summary(targets, str(run_metadata.get("run_name") or run_dir.name)),
        "target_count": len(targets),
        "user_instructions": str(scan_config.get("user_instructions") or ""),
        "parent_run_id": scan_config.get("parent_run_id"),
        "resumed_from_run_id": scan_config.get("resumed_from_run_id"),
        "llm": {
            "total": llm_total,
            "total_tokens": llm_stats.get("total_tokens", 0),
            "max_context_tokens": llm_stats.get("max_context_tokens", 0),
            "by_model": llm_stats.get("by_model", {}),
        },
        "findings_total": findings_total,
        "findings_by_severity": findings_by_severity,
        "artifacts": _artifact_metadata(run_dir, findings_total, final_report_present),
        "resume": _resume_metadata(targets, local_sources),
    }
    return manifest


def write_run_manifest(run_dir: Path, manifest: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = get_run_manifest_path(run_dir)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


def append_run_registry_entry(manifest: dict[str, Any]) -> None:
    runs_dir = get_global_runs_dir()
    runs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": manifest.get("updated_at") or _now_iso(),
        "run_id": manifest.get("run_id"),
        "run_name": manifest.get("run_name"),
        "run_dir": manifest.get("run_dir"),
        "cwd": manifest.get("cwd"),
        "status": manifest.get("status"),
        "target_summary": manifest.get("target_summary"),
        "findings_total": manifest.get("findings_total", 0),
        "manifest_path": str(Path(str(manifest.get("run_dir") or "")) / RUN_MANIFEST_FILENAME),
    }
    with get_global_run_index_path().open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True))
        handle.write("\n")


def load_run_manifest(run_dir: Path) -> dict[str, Any] | None:
    path = get_run_manifest_path(run_dir)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _legacy_manifest_from_run_dir(run_dir: Path) -> dict[str, Any] | None:
    if not run_dir.is_dir():
        return None

    findings_total = _count_vulnerabilities_from_csv(run_dir / "vulnerabilities.csv")
    report_present = (run_dir / "penetration_test_report.md").exists()
    mtime = datetime.fromtimestamp(run_dir.stat().st_mtime, tz=UTC).isoformat()
    name = run_dir.name
    parts = name.rsplit("_", 1)
    target_summary = parts[0] if len(parts) == 2 else name
    status = "completed" if report_present else "partial" if findings_total > 0 else "stopped"
    return {
        "manifest_version": 0,
        "updated_at": mtime,
        "run_id": name,
        "run_name": name,
        "run_dir": str(run_dir),
        "cwd": "",
        "status": status,
        "start_time": mtime,
        "end_time": mtime,
        "scan_mode": "",
        "model": "",
        "targets": [],
        "target_summary": target_summary,
        "target_count": 0,
        "user_instructions": "",
        "llm": {"total": {}, "total_tokens": 0, "max_context_tokens": 0, "by_model": {}},
        "findings_total": findings_total,
        "findings_by_severity": {},
        "artifacts": _artifact_metadata(run_dir, findings_total, report_present),
        "resume": {"resumable": False, "missing_paths": [], "local_sources": []},
    }


def _load_best_manifest(run_dir: Path) -> dict[str, Any] | None:
    return load_run_manifest(run_dir) or _legacy_manifest_from_run_dir(run_dir)


def _read_global_registry_records() -> list[dict[str, Any]]:
    index_path = get_global_run_index_path()
    if not index_path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        with index_path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict):
                    records.append(payload)
    except OSError:
        return []
    return records


def list_runs(
    *,
    cwd: Path | None = None,
    scope: str = "all",
    status_filter: str | None = None,
) -> list[dict[str, Any]]:
    current_cwd = (cwd or Path.cwd()).resolve()
    local_runs_dir = get_local_runs_dir(current_cwd)
    manifests: dict[str, dict[str, Any]] = {}

    if scope in {"all", "cwd"} and local_runs_dir.exists():
        for entry in sorted(local_runs_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            manifest = _load_best_manifest(entry)
            if manifest:
                manifests[str(Path(manifest["run_dir"]).resolve())] = manifest

    if scope == "all":
        for record in reversed(_read_global_registry_records()):
            run_dir_raw = str(record.get("run_dir") or "").strip()
            if not run_dir_raw:
                continue
            run_dir = Path(run_dir_raw).expanduser()
            manifest = _load_best_manifest(run_dir)
            if manifest:
                manifests.setdefault(str(run_dir.resolve()), manifest)

    runs = list(manifests.values())
    runs.sort(key=lambda item: str(item.get("updated_at") or item.get("end_time") or ""), reverse=True)

    normalized_status = (status_filter or "").strip().lower()
    if normalized_status and normalized_status not in {"any", "all"}:
        runs = [run for run in runs if str(run.get("status") or "").lower() == normalized_status]

    for run in runs:
        run["source_scope"] = (
            "cwd"
            if str(run.get("cwd") or "") and Path(str(run["cwd"])).resolve() == current_cwd
            else "global"
        )
    return runs


def build_resume_instruction(run: dict[str, Any]) -> str:
    status = str(run.get("status") or "unknown")
    findings_total = int(run.get("findings_total") or 0)
    findings_by_severity = run.get("findings_by_severity", {}) or {}
    artifact_paths = run.get("artifacts", {}) or {}
    severity_parts = []
    for severity in ("critical", "high", "medium", "low", "info"):
        count = int(findings_by_severity.get(severity) or 0)
        if count > 0:
            severity_parts.append(f"{severity}: {count}")
    findings_summary = ", ".join(severity_parts) if severity_parts else "none recorded"

    lines = [
        "System note: This scan is resuming from a previous Esprit run.",
        f"Previous run: {run.get('run_name')}",
        f"Previous status: {status}",
        f"Previous findings: {findings_total} ({findings_summary})",
    ]

    report_available = bool(artifact_paths.get("report_available"))
    if report_available:
        lines.append(f"Previous report: {artifact_paths.get('report_path')}")

    lines.append(
        "Continue from the known prior context, avoid repeating completed steps unless needed, "
        "and prioritize unresolved gaps or follow-up validation."
    )
    return "\n".join(lines)
