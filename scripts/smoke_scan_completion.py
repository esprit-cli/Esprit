#!/usr/bin/env python3
"""Run scan-completion smoke tests against low-risk public websites.

This script is intended for fast operator validation after scan-engine changes.
It runs `esprit scan` non-interactively for each target and validates that:
1) the process exits cleanly (exit code 0 or 2)
2) a checkpoint is produced
3) checkpoint `run_metadata.status` is `completed`
4) no agents are left in `running` or `failed` state
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _find_new_run_dir(before: set[str], runs_root: Path) -> Path | None:
    after = {p.name for p in runs_root.iterdir() if p.is_dir()}
    new_dirs = sorted(after - before)
    if not new_dirs:
        return None
    return runs_root / new_dirs[-1]


def _load_checkpoint(run_dir: Path) -> dict:
    checkpoint = run_dir / "checkpoint.json"
    if not checkpoint.exists():
        raise FileNotFoundError(f"Missing checkpoint: {checkpoint}")
    return json.loads(checkpoint.read_text(encoding="utf-8"))


def _validate_checkpoint(checkpoint: dict) -> tuple[bool, str]:
    run_status = checkpoint.get("run_metadata", {}).get("status")
    if run_status != "completed":
        return False, f"run_metadata.status={run_status!r}"

    agents = checkpoint.get("agents", {})
    failed = [a.get("name", "<unknown>") for a in agents.values() if a.get("status") == "failed"]
    running = [
        a.get("name", "<unknown>") for a in agents.values() if a.get("status") == "running"
    ]
    if failed:
        return False, f"failed agents: {failed}"
    if running:
        return False, f"running agents: {running}"

    return True, "completed"


def _run_one_target(
    target: str,
    model: str,
    llm_timeout: int,
    scan_mode: str,
    process_timeout_s: int,
) -> tuple[bool, str]:
    runs_root = Path("esprit_runs")
    runs_root.mkdir(exist_ok=True)
    before = {p.name for p in runs_root.iterdir() if p.is_dir()}

    env = os.environ.copy()
    env["ESPRIT_LLM"] = model
    env["LLM_TIMEOUT"] = str(llm_timeout)

    cmd = ["poetry", "run", "esprit", "scan", target, "-n", "--scan-mode", scan_mode]
    try:
        proc = subprocess.run(cmd, env=env, check=False, timeout=process_timeout_s)
    except subprocess.TimeoutExpired:
        return False, f"scan exceeded subprocess timeout ({process_timeout_s}s)"
    if proc.returncode not in {0, 2}:
        return False, f"scan exit code {proc.returncode}"

    run_dir = _find_new_run_dir(before, runs_root)
    if run_dir is None:
        return False, "no new run directory created"

    try:
        checkpoint = _load_checkpoint(run_dir)
    except Exception as e:  # noqa: BLE001
        return False, f"{run_dir}: {e}"

    ok, detail = _validate_checkpoint(checkpoint)
    if not ok:
        return False, f"{run_dir.name}: {detail}"

    vuln_count = len(checkpoint.get("vulnerability_reports", []))
    tool_count = len(checkpoint.get("tool_executions", []))
    return True, f"{run_dir.name}: vulnerabilities={vuln_count}, tools={tool_count}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test Esprit scan completion")
    parser.add_argument(
        "--model",
        default=os.getenv("ESPRIT_LLM"),
        help="Model to use (default: ESPRIT_LLM from env)",
    )
    parser.add_argument(
        "--llm-timeout",
        type=int,
        default=1800,
        help="LLM timeout in seconds (default: 1800)",
    )
    parser.add_argument(
        "--scan-mode",
        choices=["quick", "standard", "deep"],
        default="quick",
        help="Scan mode (default: quick)",
    )
    parser.add_argument(
        "--process-timeout",
        type=int,
        default=0,
        help=(
            "Hard timeout for each subprocess in seconds "
            "(default: llm-timeout + 900, minimum 600)"
        ),
    )
    parser.add_argument(
        "targets",
        nargs="*",
        default=["https://example.com", "https://example.net"],
        help="Targets to scan",
    )
    args = parser.parse_args()

    if not args.model:
        print("Missing model: pass --model or set ESPRIT_LLM", file=sys.stderr)
        return 2

    failures: list[str] = []
    process_timeout_s = args.process_timeout or max(600, args.llm_timeout + 900)
    for target in args.targets:
        print(f"\n[smoke] scanning {target}")
        ok, detail = _run_one_target(
            target=target,
            model=args.model,
            llm_timeout=args.llm_timeout,
            scan_mode=args.scan_mode,
            process_timeout_s=process_timeout_s,
        )
        status = "PASS" if ok else "FAIL"
        print(f"[smoke] {status}: {detail}")
        if not ok:
            failures.append(f"{target}: {detail}")

    if failures:
        print("\nSmoke scan failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nSmoke scan passed for all targets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
