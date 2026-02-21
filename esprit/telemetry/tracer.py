import copy
import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional
from uuid import uuid4

from esprit.telemetry import posthog


if TYPE_CHECKING:
    from collections.abc import Callable


logger = logging.getLogger(__name__)

_global_tracer: Optional["Tracer"] = None


def _cache_metrics(input_tokens: int, cached_tokens: int) -> tuple[int, float]:
    uncached_input_tokens = max(0, input_tokens - cached_tokens)
    if input_tokens <= 0:
        return uncached_input_tokens, 0.0

    cache_hit_ratio = (cached_tokens / max(input_tokens, 1)) * 100
    return uncached_input_tokens, round(min(cache_hit_ratio, 100.0), 2)


def get_global_tracer() -> Optional["Tracer"]:
    return _global_tracer


def set_global_tracer(tracer: "Tracer") -> None:
    global _global_tracer  # noqa: PLW0603
    _global_tracer = tracer


class Tracer:
    def __init__(self, run_name: str | None = None):
        self.run_name = run_name
        self.run_id = run_name or f"run-{uuid4().hex[:8]}"
        self.start_time = datetime.now(UTC).isoformat()
        self.end_time: str | None = None

        self.agents: dict[str, dict[str, Any]] = {}
        self.tool_executions: dict[int, dict[str, Any]] = {}
        self.chat_messages: list[dict[str, Any]] = []
        self.streaming_content: dict[str, str] = {}
        self.streaming_thinking: dict[str, str] = {}
        self.interrupted_content: dict[str, str] = {}

        self.vulnerability_reports: list[dict[str, Any]] = []
        self.final_scan_result: str | None = None
        self.compacting_agents: set[str] = set()

        # Track only the latest browser screenshot per agent for memory efficiency
        self.latest_browser_screenshots: dict[str, int] = {}

        self.scan_results: dict[str, Any] | None = None
        self.scan_config: dict[str, Any] | None = None
        self.run_metadata: dict[str, Any] = {
            "run_id": self.run_id,
            "run_name": self.run_name,
            "start_time": self.start_time,
            "end_time": None,
            "targets": [],
            "status": "running",
        }
        self._run_dir: Path | None = None
        self._next_execution_id = 1
        self._next_message_id = 1
        self._saved_vuln_ids: set[str] = set()

        self.vulnerability_found_callback: Callable[[dict[str, Any]], None] | None = None

        # Lock for thread-safe concurrent access to in-memory data from multiple subagent threads
        self._lock = threading.Lock()
        # Separate lock for serializing file writes to prevent checkpoint corruption
        self._save_lock = threading.Lock()
        # Best-effort periodic checkpointing for long-running scans without findings.
        self._last_checkpoint_save_monotonic = 0.0
        self._checkpoint_save_interval_s = 15.0

    def set_run_name(self, run_name: str) -> None:
        with self._lock:
            self.run_name = run_name
            self.run_id = run_name

    def _ensure_run_file_logger(self, run_dir: Path) -> None:
        run_log_path = run_dir / "run.log"

        try:
            run_log_path.touch(exist_ok=True)
        except OSError:
            logger.exception("Failed to create run log file at %s", run_log_path)
            return

        esprit_logger = logging.getLogger("esprit")
        if esprit_logger.level == logging.NOTSET or esprit_logger.level > logging.INFO:
            esprit_logger.setLevel(logging.INFO)

        resolved_path = str(run_log_path.resolve())
        for handler in esprit_logger.handlers:
            if (
                isinstance(handler, logging.FileHandler)
                and getattr(handler, "baseFilename", None) == resolved_path
            ):
                return

        try:
            file_handler = logging.FileHandler(run_log_path, encoding="utf-8")
        except OSError:
            logger.exception("Failed to attach run log handler at %s", run_log_path)
            return

        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        esprit_logger.addHandler(file_handler)

    def _set_run_status(self, status: str) -> None:
        self.run_metadata["status"] = status
        self.run_metadata["end_time"] = self.end_time

    def get_run_dir(self) -> Path:
        if self._run_dir is None:
            runs_dir = Path.cwd() / "esprit_runs"
            runs_dir.mkdir(exist_ok=True)

            run_dir_name = self.run_name if self.run_name else self.run_id
            self._run_dir = runs_dir / run_dir_name
            self._run_dir.mkdir(exist_ok=True)

        self._ensure_run_file_logger(self._run_dir)
        return self._run_dir

    def add_vulnerability_report(  # noqa: PLR0912
        self,
        title: str,
        severity: str,
        description: str | None = None,
        impact: str | None = None,
        target: str | None = None,
        technical_analysis: str | None = None,
        poc_description: str | None = None,
        poc_script_code: str | None = None,
        remediation_steps: str | None = None,
        cvss: float | None = None,
        cvss_breakdown: dict[str, str] | None = None,
        endpoint: str | None = None,
        method: str | None = None,
        cve: str | None = None,
        code_file: str | None = None,
        code_before: str | None = None,
        code_after: str | None = None,
        code_diff: str | None = None,
        cwe_id: str | None = None,
        owasp_category: str | None = None,
    ) -> str:
        # Build the report dict first (no shared state accessed here)
        report: dict[str, Any] = {
            "id": "",  # assigned atomically below
            "title": title.strip(),
            "severity": severity.lower().strip(),
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        if description:
            report["description"] = description.strip()
        if impact:
            report["impact"] = impact.strip()
        if target:
            report["target"] = target.strip()
        if technical_analysis:
            report["technical_analysis"] = technical_analysis.strip()
        if poc_description:
            report["poc_description"] = poc_description.strip()
        if poc_script_code:
            report["poc_script_code"] = poc_script_code.strip()
        if remediation_steps:
            report["remediation_steps"] = remediation_steps.strip()
        if cvss is not None:
            report["cvss"] = cvss
        if cvss_breakdown:
            report["cvss_breakdown"] = cvss_breakdown
        if endpoint:
            report["endpoint"] = endpoint.strip()
        if method:
            report["method"] = method.strip()
        if cve:
            report["cve"] = cve.strip()
        if code_file:
            report["code_file"] = code_file.strip()
        if code_before:
            report["code_before"] = code_before.strip()
        if code_after:
            report["code_after"] = code_after.strip()
        if code_diff:
            report["code_diff"] = code_diff.strip()
        if cwe_id:
            report["cwe_id"] = cwe_id.strip()
        if owasp_category:
            report["owasp_category"] = owasp_category.strip()

        # Atomically assign unique ID and append — prevents duplicate IDs under concurrency
        with self._lock:
            report_id = f"vuln-{len(self.vulnerability_reports) + 1:04d}"
            report["id"] = report_id
            self.vulnerability_reports.append(report)
        logger.info(f"Added vulnerability report: {report_id} - {title}")
        posthog.finding(severity)

        if self.vulnerability_found_callback:
            self.vulnerability_found_callback(report)

        self.save_run_data()
        return report_id

    def get_existing_vulnerabilities(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self.vulnerability_reports)

    def update_scan_final_fields(
        self,
        executive_summary: str,
        methodology: str,
        technical_analysis: str,
        recommendations: str,
    ) -> None:
        self.scan_results = {
            "scan_completed": True,
            "executive_summary": executive_summary.strip(),
            "methodology": methodology.strip(),
            "technical_analysis": technical_analysis.strip(),
            "recommendations": recommendations.strip(),
            "success": True,
        }

        self.final_scan_result = f"""# Executive Summary

{executive_summary.strip()}

# Methodology

{methodology.strip()}

# Technical Analysis

{technical_analysis.strip()}

# Recommendations

{recommendations.strip()}
"""

        self.end_time = datetime.now(UTC).isoformat()
        self._set_run_status("completed")
        logger.info("Updated scan final fields")
        self.save_run_data(mark_complete=False)
        posthog.end(self, exit_reason="finished_by_tool")

    def log_agent_creation(
        self, agent_id: str, name: str, task: str, parent_id: str | None = None
    ) -> None:
        agent_data: dict[str, Any] = {
            "id": agent_id,
            "name": name,
            "task": task,
            "status": "running",
            "parent_id": parent_id,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "tool_executions": [],
        }

        with self._lock:
            self.agents[agent_id] = agent_data

    def log_chat_message(
        self,
        content: str,
        role: str,
        agent_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        thinking_blocks: list[dict[str, Any]] | None = None,
    ) -> int:
        with self._lock:
            message_id = self._next_message_id
            self._next_message_id += 1

            message_data = {
                "message_id": message_id,
                "content": content,
                "role": role,
                "agent_id": agent_id,
                "timestamp": datetime.now(UTC).isoformat(),
                "metadata": metadata or {},
            }
            if thinking_blocks:
                message_data["thinking_blocks"] = thinking_blocks

            self.chat_messages.append(message_data)
            return message_id

    def log_tool_execution_start(self, agent_id: str, tool_name: str, args: dict[str, Any]) -> int:
        with self._lock:
            execution_id = self._next_execution_id
            self._next_execution_id += 1

            now = datetime.now(UTC).isoformat()
            execution_data = {
                "execution_id": execution_id,
                "agent_id": agent_id,
                "tool_name": tool_name,
                "args": args,
                "status": "running",
                "result": None,
                "timestamp": now,
                "started_at": now,
                "completed_at": None,
            }

            self.tool_executions[execution_id] = execution_data

            if agent_id in self.agents:
                self.agents[agent_id]["tool_executions"].append(execution_id)

            return execution_id

    def update_tool_execution(
        self, execution_id: int, status: str, result: Any | None = None
    ) -> None:
        with self._lock:
            if execution_id in self.tool_executions:
                self.tool_executions[execution_id]["status"] = status
                self.tool_executions[execution_id]["result"] = result
                self.tool_executions[execution_id]["completed_at"] = datetime.now(UTC).isoformat()
        self._maybe_save_checkpoint()

    def update_agent_status(
        self, agent_id: str, status: str, error_message: str | None = None
    ) -> None:
        with self._lock:
            if agent_id in self.agents:
                agent_data = self.agents[agent_id]
                self.agents[agent_id]["status"] = status
                self.agents[agent_id]["updated_at"] = datetime.now(UTC).isoformat()
                if error_message:
                    self.agents[agent_id]["error_message"] = error_message
                if agent_data.get("parent_id") is None:
                    if status in {"failed", "error", "sandbox_failed", "llm_failed"}:
                        self._set_run_status("failed")
                    elif status == "completed":
                        self._set_run_status("completed")
                    elif status == "stopped":
                        self._set_run_status("stopped")
                    elif status == "running":
                        self._set_run_status("running")
        self._maybe_save_checkpoint()

    def touch_agent_heartbeat(
        self, agent_id: str, phase: str, detail: str | None = None
    ) -> None:
        with self._lock:
            if agent_id not in self.agents:
                return

            timestamp = datetime.now(UTC).isoformat()
            self.agents[agent_id]["heartbeat"] = {
                "timestamp": timestamp,
                "phase": phase,
                "detail": detail,
            }
            self.agents[agent_id]["updated_at"] = timestamp
        self._maybe_save_checkpoint()

    def get_agent_heartbeat(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            agent_data = self.agents.get(agent_id)
            if not agent_data:
                return None

            heartbeat = agent_data.get("heartbeat")
            if isinstance(heartbeat, dict):
                return dict(heartbeat)
            return None

    def set_scan_config(self, config: dict[str, Any]) -> None:
        with self._lock:
            self.scan_config = config
            self.run_metadata.update(
                {
                    "targets": config.get("targets", []),
                    "user_instructions": config.get("user_instructions", ""),
                    "max_iterations": config.get("max_iterations", 200),
                    "scan_mode": config.get("scan_mode", ""),
                    "model": config.get("model", ""),
                    "estimated_cost_low": config.get("estimated_cost_low"),
                    "estimated_cost_mid": config.get("estimated_cost_mid"),
                    "estimated_cost_high": config.get("estimated_cost_high"),
                    "estimated_time_low_min": config.get("estimated_time_low_min"),
                    "estimated_time_mid_min": config.get("estimated_time_mid_min"),
                    "estimated_time_high_min": config.get("estimated_time_high_min"),
                }
            )
        self.get_run_dir()

    def save_run_data(self, mark_complete: bool = False) -> None:  # noqa: PLR0912, PLR0915
        # Final saves always wait; intermediate saves skip if one is already in progress
        acquired = self._save_lock.acquire(blocking=mark_complete)
        if not acquired:
            return
        try:
            run_dir = self.get_run_dir()
            if mark_complete:
                self.end_time = datetime.now(UTC).isoformat()
                self.run_metadata["end_time"] = self.end_time
                if self.run_metadata.get("status") == "running":
                    self.run_metadata["status"] = "completed"

            # Save full checkpoint
            self.save_checkpoint(run_dir / "checkpoint.json")

            if self.final_scan_result:
                penetration_test_report_file = run_dir / "penetration_test_report.md"
                with penetration_test_report_file.open("w", encoding="utf-8") as f:
                    f.write("# Security Penetration Test Report\n\n")
                    f.write(
                        f"**Generated:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}\n\n"
                    )
                    f.write(f"{self.final_scan_result}\n")
                logger.info(
                    f"Saved final penetration test report to: {penetration_test_report_file}"
                )

            if self.vulnerability_reports:
                vuln_dir = run_dir / "vulnerabilities"
                vuln_dir.mkdir(exist_ok=True)

                new_reports = [
                    report
                    for report in self.vulnerability_reports
                    if report["id"] not in self._saved_vuln_ids
                ]

                severity_order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
                sorted_reports = sorted(
                    self.vulnerability_reports,
                    key=lambda x: (severity_order.get(x["severity"], 5), x["timestamp"]),
                )

                for report in new_reports:
                    vuln_file = vuln_dir / f"{report['id']}.md"
                    with vuln_file.open("w", encoding="utf-8") as f:
                        f.write(f"# {report.get('title', 'Untitled Vulnerability')}\n\n")
                        f.write(f"**ID:** {report.get('id', 'unknown')}\n")
                        f.write(f"**Severity:** {report.get('severity', 'unknown').upper()}\n")
                        f.write(f"**Found:** {report.get('timestamp', 'unknown')}\n")

                        metadata_fields: list[tuple[str, Any]] = [
                            ("Target", report.get("target")),
                            ("Endpoint", report.get("endpoint")),
                            ("Method", report.get("method")),
                            ("CVE", report.get("cve")),
                            ("CWE", report.get("cwe_id")),
                            ("OWASP", report.get("owasp_category")),
                        ]
                        cvss_score = report.get("cvss")
                        if cvss_score is not None:
                            metadata_fields.append(("CVSS", cvss_score))

                        for label, value in metadata_fields:
                            if value:
                                f.write(f"**{label}:** {value}\n")

                        f.write("\n## Description\n\n")
                        desc = report.get("description") or "No description provided."
                        f.write(f"{desc}\n\n")

                        if report.get("impact"):
                            f.write("## Impact\n\n")
                            f.write(f"{report['impact']}\n\n")

                        if report.get("technical_analysis"):
                            f.write("## Technical Analysis\n\n")
                            f.write(f"{report['technical_analysis']}\n\n")

                        if report.get("poc_description") or report.get("poc_script_code"):
                            f.write("## Proof of Concept\n\n")
                            if report.get("poc_description"):
                                f.write(f"{report['poc_description']}\n\n")
                            if report.get("poc_script_code"):
                                f.write("```\n")
                                f.write(f"{report['poc_script_code']}\n")
                                f.write("```\n\n")

                        if report.get("code_file") or report.get("code_diff"):
                            f.write("## Code Analysis\n\n")
                            if report.get("code_file"):
                                f.write(f"**File:** {report['code_file']}\n\n")
                            if report.get("code_diff"):
                                f.write("**Changes:**\n")
                                f.write("```diff\n")
                                f.write(f"{report['code_diff']}\n")
                                f.write("```\n\n")

                        if report.get("remediation_steps"):
                            f.write("## Remediation\n\n")
                            f.write(f"{report['remediation_steps']}\n\n")

                    self._saved_vuln_ids.add(report["id"])

                vuln_csv_file = run_dir / "vulnerabilities.csv"
                with vuln_csv_file.open("w", encoding="utf-8", newline="") as f:
                    import csv

                    fieldnames = ["id", "title", "severity", "cwe_id", "owasp_category", "timestamp", "file"]
                    writer = csv.DictWriter(f, fieldnames=fieldnames)
                    writer.writeheader()

                    for report in sorted_reports:
                        writer.writerow(
                            {
                                "id": report["id"],
                                "title": report["title"],
                                "severity": report["severity"].upper(),
                                "cwe_id": report.get("cwe_id", ""),
                                "owasp_category": report.get("owasp_category", ""),
                                "timestamp": report["timestamp"],
                                "file": f"vulnerabilities/{report['id']}.md",
                            }
                        )

                if new_reports:
                    logger.info(
                        f"Saved {len(new_reports)} new vulnerability report(s) to: {vuln_dir}"
                    )
                logger.info(f"Updated vulnerability index: {vuln_csv_file}")

            logger.info(f"📊 Essential scan data saved to: {run_dir}")

        except (OSError, RuntimeError):
            logger.exception("Failed to save scan data")
        finally:
            self._save_lock.release()

    def _build_checkpoint_data(self) -> dict[str, Any]:
        from esprit.tools.agents_graph.agents_graph_actions import _agent_instances

        with self._lock:
            data = {
                "run_id": self.run_id,
                "run_name": self.run_name,
                "start_time": self.start_time,
                "end_time": self.end_time,
                "agents": copy.deepcopy(self.agents),
                "tool_executions": copy.deepcopy(self.tool_executions),
                "chat_messages": copy.deepcopy(self.chat_messages),
                "vulnerability_reports": copy.deepcopy(self.vulnerability_reports),
                "scan_results": copy.deepcopy(self.scan_results),
                "scan_config": copy.deepcopy(self.scan_config),
                "run_metadata": copy.deepcopy(self.run_metadata),
                "next_execution_id": self._next_execution_id,
                "next_message_id": self._next_message_id,
                "agent_states": {},
            }

        # Snapshot agent registry first; it can change while agents spawn/exit.
        agent_items = list(_agent_instances.items())
        for agent_id, agent in agent_items:
            if not hasattr(agent, "state"):
                continue
            try:
                data["agent_states"][agent_id] = copy.deepcopy(agent.state.model_dump())
            except Exception:  # noqa: BLE001
                logger.debug(
                    "Skipping checkpoint serialization for agent state %s",
                    agent_id,
                    exc_info=True,
                )

        return data

    def save_checkpoint(self, filepath: Path) -> None:
        """Saves the full state of the tracer and agents to a JSON file."""
        import json

        data = self._build_checkpoint_data()
        tmp_path = filepath.with_suffix(f"{filepath.suffix}.tmp")

        with tmp_path.open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

        tmp_path.replace(filepath)
        logger.info(f"Checkpoint saved to {filepath}")

    def _maybe_save_checkpoint(self) -> None:
        if self._run_dir is None:
            return

        now = time.monotonic()
        if (
            self._last_checkpoint_save_monotonic > 0
            and now - self._last_checkpoint_save_monotonic < self._checkpoint_save_interval_s
        ):
            return

        acquired = self._save_lock.acquire(blocking=False)
        if not acquired:
            return

        try:
            run_dir = self.get_run_dir()
            self.save_checkpoint(run_dir / "checkpoint.json")
            self._last_checkpoint_save_monotonic = now
        except Exception:
            logger.exception("Failed to write periodic checkpoint")
        finally:
            self._save_lock.release()

    @classmethod
    def load_from_dir(cls, run_dir: "Path | str") -> "Tracer | None":
        """Load a Tracer from a saved run directory.

        Looks for ``checkpoint.json`` inside *run_dir* and delegates to
        :meth:`load_checkpoint`.  Returns ``None`` when the checkpoint file is
        absent or cannot be parsed, so callers can distinguish "run not found"
        from a hard error without catching exceptions themselves.
        """
        checkpoint_path = Path(run_dir) / "checkpoint.json"
        if not checkpoint_path.exists():
            return None
        try:
            tracer = cls.load_checkpoint(checkpoint_path)
            tracer._run_dir = Path(run_dir)
            return tracer
        except Exception as exc:
            logger.warning("Failed to load tracer from %s: %s", run_dir, exc)
            return None

    @classmethod
    def load_checkpoint(cls, filepath: Path) -> "Tracer":
        """Loads a tracer from a checkpoint JSON file."""
        import json

        with filepath.open("r", encoding="utf-8") as f:
            data = json.load(f)

        tracer = cls(run_name=data.get("run_name"))
        tracer.run_id = data.get("run_id", tracer.run_id)
        tracer.start_time = data.get("start_time", tracer.start_time)
        tracer.end_time = data.get("end_time")

        tracer.agents = data.get("agents", {})
        # Convert keys back to int for tool_executions
        tracer.tool_executions = {int(k): v for k, v in data.get("tool_executions", {}).items()}
        tracer.chat_messages = data.get("chat_messages", [])
        tracer.vulnerability_reports = data.get("vulnerability_reports", [])
        tracer.scan_results = data.get("scan_results")
        tracer.scan_config = data.get("scan_config")
        tracer.run_metadata = data.get("run_metadata", tracer.run_metadata)
        tracer._next_execution_id = data.get("next_execution_id", 1)
        tracer._next_message_id = data.get("next_message_id", 1)

        # Restore saved vuln IDs to prevent duplication
        tracer._saved_vuln_ids = {r["id"] for r in tracer.vulnerability_reports}

        # Note: Agent states need to be rehydrated by the caller/runtime
        # We store them in a temporary attribute for the runtime to access
        tracer._loaded_agent_states = data.get("agent_states", {})

        return tracer

    def _calculate_duration(self) -> float:
        try:
            start = datetime.fromisoformat(self.start_time.replace("Z", "+00:00"))
            if self.end_time:
                end = datetime.fromisoformat(self.end_time.replace("Z", "+00:00"))
                return (end - start).total_seconds()
        except (ValueError, TypeError):
            pass
        return 0.0

    def get_agent_tools(self, agent_id: str) -> list[dict[str, Any]]:
        return [
            exec_data
            for exec_data in list(self.tool_executions.values())
            if exec_data.get("agent_id") == agent_id
        ]

    def get_real_tool_count(self) -> int:
        return sum(
            1
            for exec_data in list(self.tool_executions.values())
            if exec_data.get("tool_name") not in ["scan_start_info", "subagent_start_info"]
        )

    def get_total_llm_stats(self) -> dict[str, Any]:
        from esprit.tools.agents_graph.agents_graph_actions import _agent_instances

        total_stats = {
            "input_tokens": 0,
            "output_tokens": 0,
            "cached_tokens": 0,
            "cost": 0.0,
            "requests": 0,
        }
        max_context = 0
        by_model: dict[str, dict[str, Any]] = {}
        by_agent: dict[str, dict[str, Any]] = {}

        for agent_id, agent_instance in _agent_instances.items():
            if hasattr(agent_instance, "llm") and hasattr(agent_instance.llm, "_total_stats"):
                agent_stats = agent_instance.llm._total_stats
                model_name = "unknown"
                if hasattr(agent_instance.llm, "config") and hasattr(agent_instance.llm.config, "model_name"):
                    model_name = str(agent_instance.llm.config.model_name or "unknown")

                total_stats["input_tokens"] += agent_stats.input_tokens
                total_stats["output_tokens"] += agent_stats.output_tokens
                total_stats["cached_tokens"] += agent_stats.cached_tokens
                total_stats["cost"] += agent_stats.cost
                total_stats["requests"] += agent_stats.requests

                model_stats = by_model.setdefault(
                    model_name,
                    {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cached_tokens": 0,
                        "cost": 0.0,
                        "requests": 0,
                    },
                )
                model_stats["input_tokens"] += agent_stats.input_tokens
                model_stats["output_tokens"] += agent_stats.output_tokens
                model_stats["cached_tokens"] += agent_stats.cached_tokens
                model_stats["cost"] += agent_stats.cost
                model_stats["requests"] += agent_stats.requests

                agent_uncached, agent_cache_ratio = _cache_metrics(
                    int(agent_stats.input_tokens), int(agent_stats.cached_tokens)
                )
                by_agent[str(agent_id)] = {
                    "model": model_name,
                    "input_tokens": int(agent_stats.input_tokens),
                    "output_tokens": int(agent_stats.output_tokens),
                    "cached_tokens": int(agent_stats.cached_tokens),
                    "uncached_input_tokens": agent_uncached,
                    "cache_hit_ratio": agent_cache_ratio,
                    "cost": round(float(agent_stats.cost), 4),
                    "requests": int(agent_stats.requests),
                }

                last_raw = getattr(agent_stats, "last_input_tokens", 0)
                try:
                    last = int(last_raw)
                except (TypeError, ValueError):
                    last = 0
                if last > max_context:
                    max_context = last

        total_stats["cost"] = round(total_stats["cost"], 4)
        for model_stats in by_model.values():
            model_stats["cost"] = round(float(model_stats["cost"]), 4)
            uncached, cache_ratio = _cache_metrics(
                int(model_stats["input_tokens"]), int(model_stats["cached_tokens"])
            )
            model_stats["uncached_input_tokens"] = uncached
            model_stats["cache_hit_ratio"] = cache_ratio

        total_uncached, total_cache_ratio = _cache_metrics(
            int(total_stats["input_tokens"]), int(total_stats["cached_tokens"])
        )
        total_stats["uncached_input_tokens"] = total_uncached
        total_stats["cache_hit_ratio"] = total_cache_ratio

        return {
            "total": total_stats,
            "total_tokens": total_stats["input_tokens"] + total_stats["output_tokens"],
            "max_context_tokens": max_context,
            "uncached_input_tokens": total_uncached,
            "cache_hit_ratio": total_cache_ratio,
            "by_model": by_model,
            "by_agent": by_agent,
        }

    def update_streaming_content(self, agent_id: str, content: str) -> None:
        self.streaming_content[agent_id] = content

    def clear_streaming_content(self, agent_id: str) -> None:
        self.streaming_content.pop(agent_id, None)

    def get_streaming_content(self, agent_id: str) -> str | None:
        return self.streaming_content.get(agent_id)

    def update_streaming_thinking(self, agent_id: str, thinking: str) -> None:
        self.streaming_thinking[agent_id] = thinking

    def clear_streaming_thinking(self, agent_id: str) -> None:
        self.streaming_thinking.pop(agent_id, None)

    def get_streaming_thinking(self, agent_id: str) -> str | None:
        return self.streaming_thinking.get(agent_id)

    def finalize_streaming_as_interrupted(self, agent_id: str) -> str | None:
        content = self.streaming_content.pop(agent_id, None)
        if content and content.strip():
            self.interrupted_content[agent_id] = content
            self.log_chat_message(
                content=content,
                role="assistant",
                agent_id=agent_id,
                metadata={"interrupted": True},
            )
            return content

        return self.interrupted_content.pop(agent_id, None)

    def cleanup(self) -> None:
        if getattr(self, "_cleanup_done", False):
            return
        self._cleanup_done = True
        if self.end_time is None:
            self.end_time = datetime.now(UTC).isoformat()
        if self.run_metadata.get("status") == "running":
            root_statuses = [
                data.get("status", "")
                for data in self.agents.values()
                if data.get("parent_id") is None
            ]
            if self.final_scan_result:
                self._set_run_status("completed")
            elif any(s in {"failed", "error", "sandbox_failed", "llm_failed"} for s in root_statuses):
                self._set_run_status("failed")
            elif any(s == "stopped" for s in root_statuses):
                self._set_run_status("stopped")
            elif any(s == "completed" for s in root_statuses):
                self._set_run_status("completed")
            else:
                self._set_run_status("stopped")
        else:
            self._set_run_status(str(self.run_metadata.get("status", "stopped")))
        self.save_run_data(mark_complete=True)
        # Persist session cost to lifetime total
        try:
            stats = self.get_total_llm_stats()
            session_cost = stats["total"].get("cost", 0.0)
            if session_cost > 0:
                from esprit.llm.pricing import add_session_cost

                add_session_cost(session_cost)
        except Exception:
            logger.debug("Failed to persist session cost", exc_info=True)
