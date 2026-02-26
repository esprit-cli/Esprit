"""Generate discovery benchmark metrics from replayable trace scenarios."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .integration import DiscoveryIntegration


def load_trace(path: str | Path) -> list[dict[str, Any]]:
    trace_path = Path(path).expanduser().resolve()
    data = json.loads(trace_path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Trace file must contain a list: {trace_path}")
    return [entry for entry in data if isinstance(entry, dict)]


def _outcome_for_task(task: dict[str, Any], index: int) -> tuple[bool, list[str] | None, str]:
    vuln_hint = str(task.get("suggested_name", "")).lower()
    likely_validated = any(
        marker in vuln_hint
        for marker in ("injection", "authorization", "idor", "auth", "xss")
    )
    if likely_validated or index % 2 == 0:
        return True, [f"Benchmark validation for {task.get('hypothesis_id', 'unknown')}"], "validated"
    return False, None, "falsified"


def run_benchmark(
    trace_events: list[dict[str, Any]],
    *,
    max_cycles: int = 6,
    max_tasks_per_cycle: int = 3,
) -> dict[str, Any]:
    integration = DiscoveryIntegration(enabled=True)

    for event in trace_events:
        tool_name = str(event.get("tool_name") or "")
        tool_args = event.get("tool_args") or {}
        result = event.get("result")
        if not tool_name:
            continue
        if not isinstance(tool_args, dict):
            tool_args = {}
        integration.process_tool_result(tool_name, tool_args, result)

    for cycle in range(max_cycles):
        tasks = integration.scheduler.get_next_tasks(max_tasks=max_tasks_per_cycle)
        if not tasks:
            break

        for index, task in enumerate(tasks):
            hypothesis_id = str(task.get("hypothesis_id") or "")
            if not hypothesis_id:
                continue

            agent_id = f"bench_agent_{cycle}_{index}"
            experiment_id = integration.scheduler.mark_scheduled(hypothesis_id, agent_id)
            if not experiment_id:
                continue

            success, findings, outcome = _outcome_for_task(task, index)
            integration.complete_experiment_from_agent_result(
                agent_id,
                success=success,
                result_summary=outcome,
                findings=findings,
            )

    data = integration.get_persistence_data()
    metrics = dict(data.get("discovery_metrics", {}) or {})
    completed = int(metrics.get("completed_experiments", 0) or 0)
    validated = int(metrics.get("validated_hypotheses", 0) or 0)
    metrics["validated_finding_rate"] = validated / completed if completed > 0 else 0.0
    metrics["benchmark_events_processed"] = len(trace_events)
    return metrics


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Replay discovery traces and emit benchmark metrics.")
    parser.add_argument(
        "--trace",
        required=True,
        help="Path to JSON array of trace events",
    )
    parser.add_argument(
        "--output",
        required=True,
        help="Path for generated discovery_metrics.json",
    )
    parser.add_argument("--max-cycles", type=int, default=6)
    parser.add_argument("--max-tasks-per-cycle", type=int, default=3)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    events = load_trace(args.trace)
    metrics = run_benchmark(
        events,
        max_cycles=max(1, args.max_cycles),
        max_tasks_per_cycle=max(1, args.max_tasks_per_cycle),
    )

    output_path = Path(args.output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
