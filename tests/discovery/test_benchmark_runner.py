"""Tests for discovery benchmark trace runner."""

import json
from pathlib import Path

from esprit.discovery.benchmark_runner import load_trace, main, run_benchmark


def _sample_events() -> list[dict[str, object]]:
    return [
        {
            "tool_name": "list_requests",
            "tool_args": {},
            "result": {
                "requests": [
                    {
                        "id": "req_1",
                        "method": "GET",
                        "host": "example.com",
                        "path": "/api/admin",
                        "response": {"statusCode": 403, "roundtripTime": 100},
                    },
                    {
                        "id": "req_2",
                        "method": "GET",
                        "host": "example.com",
                        "path": "/api/slow",
                        "response": {"statusCode": 200, "roundtripTime": 9000},
                    },
                ]
            },
        },
        {
            "tool_name": "send_request",
            "tool_args": {"method": "POST", "url": "https://example.com/api/search"},
            "result": {
                "id": "req_3",
                "status_code": 500,
                "body": "You have an error in your SQL syntax near ' OR 1=1 --'",
            },
        },
    ]


class TestBenchmarkRunner:
    def test_run_benchmark_generates_expected_metrics(self) -> None:
        metrics = run_benchmark(_sample_events())
        assert metrics["total_hypotheses"] > 0
        assert metrics["completed_experiments"] > 0
        assert metrics["benchmark_events_processed"] == 2

    def test_load_trace_reads_list(self, tmp_path: Path) -> None:
        path = tmp_path / "trace.json"
        path.write_text(json.dumps(_sample_events()), encoding="utf-8")
        events = load_trace(path)
        assert len(events) == 2

    def test_main_writes_output_file(self, tmp_path: Path) -> None:
        trace = tmp_path / "trace.json"
        output = tmp_path / "metrics.json"
        trace.write_text(json.dumps(_sample_events()), encoding="utf-8")

        code = main(["--trace", str(trace), "--output", str(output)])
        assert code == 0
        data = json.loads(output.read_text(encoding="utf-8"))
        assert data["total_hypotheses"] > 0
