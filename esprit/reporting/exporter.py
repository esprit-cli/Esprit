import csv
import base64
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from esprit.utils.resource_paths import get_esprit_resource_path

if TYPE_CHECKING:
    from esprit.telemetry.tracer import Tracer

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _sorted_vulns(vulns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        vulns,
        key=lambda v: (SEVERITY_ORDER.get(v.get("severity", "info"), 5), v.get("timestamp", "")),
    )


class ReportExporter:
    def __init__(self, tracer: "Tracer"):
        self.tracer = tracer
        self.template_dir = Path(__file__).parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(self.template_dir),
            autoescape=select_autoescape(["html", "xml", "jinja"]),
        )

    def generate_html_report(self, output_path: str | Path) -> Path:
        """Generates a comprehensive HTML report."""
        template = self.env.get_template("report.html.jinja")

        # Calculate summary stats
        vulns = self.tracer.vulnerability_reports
        summary = {
            "critical": len([v for v in vulns if v["severity"] == "critical"]),
            "high": len([v for v in vulns if v["severity"] == "high"]),
            "medium": len([v for v in vulns if v["severity"] == "medium"]),
            "low": len([v for v in vulns if v["severity"] == "low"]),
            "info": len([v for v in vulns if v["severity"] == "info"]),
        }

        # Prepare context
        context = {
            "run_id": self.tracer.run_id,
            "run_name": self.tracer.run_name,
            "start_time": self.tracer.start_time,
            "duration": f"{self.tracer._calculate_duration():.2f}",
            "status": self.tracer.run_metadata.get("status", "unknown"),
            "summary": summary,
            "vulnerabilities": vulns,
            "executive_summary": self.tracer.scan_results.get("executive_summary", "") if self.tracer.scan_results else "",
            "methodology": self.tracer.scan_results.get("methodology", "") if self.tracer.scan_results else "",
            "recommendations": self.tracer.scan_results.get("recommendations", "") if self.tracer.scan_results else "",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        rendered = template.render(context)

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(rendered, encoding="utf-8")

        logger.info(f"Generated HTML report at: {output_file}")
        return output_file

    def generate_timelapse(self, output_path: str | Path) -> Path:
        """Generates a timelapse HTML file."""
        template = self.env.get_template("timelapse.html.jinja")

        # Prepare data for the timelapse
        # We need chronological events: tool calls, agent messages, vulnerability findings

        events = []

        # Add messages
        for msg in self.tracer.chat_messages:
            events.append({
                "type": "message",
                "timestamp": msg["timestamp"],
                "data": msg
            })

        # Add tool executions
        for exec_id, tool_exec in self.tracer.tool_executions.items():
            events.append({
                "type": "tool_start",
                "timestamp": tool_exec["started_at"],
                "data": tool_exec
            })
            if tool_exec.get("completed_at"):
                events.append({
                    "type": "tool_end",
                    "timestamp": tool_exec["completed_at"],
                    "data": {
                        "execution_id": exec_id,
                        "status": tool_exec["status"],
                        "result": tool_exec.get("result")
                    }
                })

        # Add vulnerabilities
        for vuln in self.tracer.vulnerability_reports:
            events.append({
                "type": "vulnerability",
                "timestamp": vuln["timestamp"],
                "data": vuln
            })

        # Sort by timestamp
        events.sort(key=lambda x: x["timestamp"])

        context = {
            "run_id": self.tracer.run_id,
            "events_b64": base64.b64encode(
                json.dumps(events, default=str, ensure_ascii=False).encode("utf-8")
            ).decode("ascii"),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        rendered = template.render(context)

        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(rendered, encoding="utf-8")

        logger.info(f"Generated timelapse at: {output_file}")
        return output_file

    def _get_output_dir(self) -> Path:
        return self.tracer.get_run_dir()

    def export_json(self, output_path: str | Path | None = None) -> Path:
        """Export vulnerabilities as JSON."""
        vulns = _sorted_vulns(self.tracer.vulnerability_reports)
        data = {
            "run_id": self.tracer.run_id,
            "run_name": self.tracer.run_name,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "total": len(vulns),
            "summary": {
                sev: len([v for v in vulns if v.get("severity") == sev])
                for sev in ("critical", "high", "medium", "low", "info")
            },
            "vulnerabilities": vulns,
        }
        out = Path(output_path) if output_path else self._get_output_dir() / "vulnerabilities.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info(f"Exported JSON: {out}")
        return out

    def export_csv(self, output_path: str | Path | None = None) -> Path:
        """Export vulnerabilities as CSV."""
        vulns = _sorted_vulns(self.tracer.vulnerability_reports)
        fieldnames = [
            "id", "title", "severity", "cvss", "target", "endpoint", "method",
            "cve", "cwe_id", "owasp_category", "description", "impact",
            "remediation_steps", "timestamp",
        ]
        out = Path(output_path) if output_path else self._get_output_dir() / "vulnerabilities.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for v in vulns:
                writer.writerow(v)
        logger.info(f"Exported CSV: {out}")
        return out

    def export_markdown(self, output_path: str | Path | None = None) -> Path:
        """Export vulnerabilities as a single Markdown report."""
        vulns = _sorted_vulns(self.tracer.vulnerability_reports)
        lines: list[str] = []
        lines.append("# Vulnerability Report")
        lines.append("")
        lines.append(f"**Run:** {self.tracer.run_name}  ")
        lines.append(f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  ")
        lines.append(f"**Total Findings:** {len(vulns)}")
        lines.append("")

        # Summary table
        lines.append("| Severity | Count |")
        lines.append("|----------|-------|")
        for sev in ("critical", "high", "medium", "low", "info"):
            count = len([v for v in vulns if v.get("severity") == sev])
            if count:
                lines.append(f"| {sev.upper()} | {count} |")
        lines.append("")
        lines.append("---")
        lines.append("")

        for vuln in vulns:
            title = vuln.get("title", "Untitled")
            lines.append(f"## {title}")
            lines.append("")
            if vuln.get("id"):
                lines.append(f"**ID:** {vuln['id']}  ")
            if vuln.get("severity"):
                lines.append(f"**Severity:** {vuln['severity'].upper()}  ")
            if vuln.get("cvss") is not None:
                lines.append(f"**CVSS:** {vuln['cvss']}  ")
            if vuln.get("target"):
                lines.append(f"**Target:** {vuln['target']}  ")
            if vuln.get("endpoint"):
                lines.append(f"**Endpoint:** {vuln['endpoint']}  ")
            if vuln.get("method"):
                lines.append(f"**Method:** {vuln['method']}  ")
            if vuln.get("cve"):
                lines.append(f"**CVE:** {vuln['cve']}  ")
            if vuln.get("cwe_id"):
                lines.append(f"**CWE:** {vuln['cwe_id']}  ")
            if vuln.get("owasp_category"):
                lines.append(f"**OWASP:** {vuln['owasp_category']}  ")
            if vuln.get("timestamp"):
                lines.append(f"**Found:** {vuln['timestamp']}  ")
            lines.append("")

            if vuln.get("description"):
                lines.append("### Description")
                lines.append("")
                lines.append(vuln["description"])
                lines.append("")
            if vuln.get("impact"):
                lines.append("### Impact")
                lines.append("")
                lines.append(vuln["impact"])
                lines.append("")
            if vuln.get("technical_analysis"):
                lines.append("### Technical Analysis")
                lines.append("")
                lines.append(vuln["technical_analysis"])
                lines.append("")
            if vuln.get("poc_description") or vuln.get("poc_script_code"):
                lines.append("### Proof of Concept")
                lines.append("")
                if vuln.get("poc_description"):
                    lines.append(vuln["poc_description"])
                    lines.append("")
                if vuln.get("poc_script_code"):
                    lines.append("```")
                    lines.append(vuln["poc_script_code"])
                    lines.append("```")
                    lines.append("")
            if vuln.get("remediation_steps"):
                lines.append("### Remediation")
                lines.append("")
                lines.append(vuln["remediation_steps"])
                lines.append("")
            lines.append("---")
            lines.append("")

        out = Path(output_path) if output_path else self._get_output_dir() / "vulnerabilities.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Exported Markdown: {out}")
        return out

    def export_sarif(self, output_path: str | Path | None = None) -> Path:
        """Export vulnerabilities in SARIF 2.1.0 format for CI/CD integration."""
        vulns = _sorted_vulns(self.tracer.vulnerability_reports)

        severity_to_sarif = {
            "critical": "error",
            "high": "error",
            "medium": "warning",
            "low": "note",
            "info": "note",
        }

        rules: list[dict[str, Any]] = []
        results: list[dict[str, Any]] = []
        seen_rule_ids: set[str] = set()

        for vuln in vulns:
            rule_id = vuln.get("cwe_id") or vuln.get("id", "unknown")
            if rule_id not in seen_rule_ids:
                seen_rule_ids.add(rule_id)
                rule: dict[str, Any] = {
                    "id": rule_id,
                    "shortDescription": {"text": vuln.get("title", "Unknown")},
                }
                if vuln.get("description"):
                    rule["fullDescription"] = {"text": vuln["description"]}
                if vuln.get("cvss") is not None:
                    rule["properties"] = {"security-severity": str(vuln["cvss"])}
                rules.append(rule)

            result: dict[str, Any] = {
                "ruleId": rule_id,
                "level": severity_to_sarif.get(vuln.get("severity", "info"), "note"),
                "message": {"text": vuln.get("description") or vuln.get("title", "")},
            }

            # Location from target/endpoint/code_file
            locations: list[dict[str, Any]] = []
            if vuln.get("code_file"):
                locations.append({
                    "physicalLocation": {
                        "artifactLocation": {"uri": vuln["code_file"]},
                    }
                })
            elif vuln.get("endpoint"):
                locations.append({
                    "physicalLocation": {
                        "artifactLocation": {"uri": vuln["endpoint"]},
                    }
                })
            if locations:
                result["locations"] = locations

            # Fixes / remediation
            if vuln.get("remediation_steps"):
                result["fixes"] = [{"description": {"text": vuln["remediation_steps"]}}]

            results.append(result)

        sarif = {
            "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/main/sarif-2.1/schema/sarif-schema-2.1.0.json",
            "version": "2.1.0",
            "runs": [{
                "tool": {
                    "driver": {
                        "name": "Esprit",
                        "informationUri": "https://github.com/esprit-agent/esprit",
                        "version": "1.0.0",
                        "rules": rules,
                    }
                },
                "results": results,
            }],
        }

        out = Path(output_path) if output_path else self._get_output_dir() / "vulnerabilities.sarif"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(sarif, indent=2, default=str), encoding="utf-8")
        logger.info(f"Exported SARIF: {out}")
        return out
