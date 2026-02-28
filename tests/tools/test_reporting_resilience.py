from __future__ import annotations

from typing import Any

from esprit.tools.executor import _validate_tool_arguments
from esprit.tools.reporting.reporting_actions import create_vulnerability_report


class _DummyTracer:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_existing_vulnerabilities(self) -> list[dict[str, Any]]:
        return []

    def add_vulnerability_report(self, **kwargs: Any) -> str:
        self.calls.append(kwargs)
        return "vuln-0001"


def test_executor_allows_sparse_vulnerability_report_params() -> None:
    error = _validate_tool_arguments("create_vulnerability_report", {"title": "SQL Injection"})
    assert error is None


def test_create_vulnerability_report_defaults_missing_fields(monkeypatch: Any) -> None:
    tracer = _DummyTracer()
    monkeypatch.setattr("esprit.telemetry.tracer.get_global_tracer", lambda: tracer)

    result = create_vulnerability_report(title="Open Redirect")

    assert result["success"] is True
    assert result["report_id"] == "vuln-0001"
    assert result["warnings"]

    assert tracer.calls
    stored = tracer.calls[0]
    assert stored["title"] == "Open Redirect"
    assert stored["target"] == "unknown target"
    assert stored["cvss_breakdown"]["attack_vector"] == "N"


def test_create_vulnerability_report_normalizes_invalid_cvss(monkeypatch: Any) -> None:
    tracer = _DummyTracer()
    monkeypatch.setattr("esprit.telemetry.tracer.get_global_tracer", lambda: tracer)

    result = create_vulnerability_report(
        title="SQL Injection",
        description="Raw SQL used with untrusted input.",
        target="https://example.test",
        attack_vector="Z",
        attack_complexity="X",
        privileges_required="Q",
    )

    assert result["success"] is True
    assert any("attack_vector=Z invalid" in warning for warning in result["warnings"])
    stored = tracer.calls[0]
    assert stored["cvss_breakdown"]["attack_vector"] == "N"
    assert stored["cvss_breakdown"]["attack_complexity"] == "L"
    assert stored["cvss_breakdown"]["privileges_required"] == "N"
