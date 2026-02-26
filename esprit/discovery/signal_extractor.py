"""Extract security-relevant signals from tool execution results.

Signals are lightweight observations derived from proxy, terminal, and browser
tool outputs. They feed into the hypothesis generator.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .models import AnomalyEvent, AnomalyType, EvidenceRef


logger = logging.getLogger(__name__)

# HTTP status codes that indicate potential security issues
_INTERESTING_STATUS_CODES = {
    401, 403, 405, 500, 501, 502, 503,
}

# Patterns that suggest error information leakage
_ERROR_LEAK_PATTERNS = [
    re.compile(r"stack\s*trace", re.IGNORECASE),
    re.compile(r"traceback\s*\(most\s*recent", re.IGNORECASE),
    re.compile(r"exception\s+in\s+thread", re.IGNORECASE),
    re.compile(r"(sql|mysql|postgres|oracle|sqlite)\s*(error|exception|syntax)", re.IGNORECASE),
    re.compile(r"at\s+[\w.]+\([\w.]+:\d+\)", re.IGNORECASE),  # Java stack frame
    re.compile(r"File\s+\"[^\"]+\",\s+line\s+\d+", re.IGNORECASE),  # Python stack frame
    re.compile(r"internal\s+server\s+error", re.IGNORECASE),
    re.compile(r"debug\s*mode\s*[=:]\s*(true|on|1|enabled)", re.IGNORECASE),
]

# Patterns suggesting injection entry points
_INJECTION_SIGNAL_PATTERNS = [
    re.compile(r"(syntax\s+error|unterminated|unexpected\s+token)", re.IGNORECASE),
    re.compile(r"(you\s+have\s+an\s+error\s+in\s+your\s+sql)", re.IGNORECASE),
    re.compile(r"(quoted\s+string\s+not\s+properly\s+terminated)", re.IGNORECASE),
    re.compile(r"(unclosed\s+quotation\s+mark)", re.IGNORECASE),
    re.compile(r"<script[^>]*>", re.IGNORECASE),  # reflected XSS indicator
]

# Timing thresholds (milliseconds)
_SLOW_RESPONSE_THRESHOLD_MS = 5000
_TIMING_ANOMALY_RATIO = 3.0  # response N times slower than baseline


class SignalExtractor:
    """Extracts anomaly signals from tool execution results."""

    def __init__(self) -> None:
        self._baseline_timings: dict[str, list[float]] = {}

    def extract_from_tool_result(
        self,
        tool_name: str,
        tool_args: dict[str, Any],
        result: Any,
    ) -> list[AnomalyEvent]:
        """Extract anomaly events from a single tool execution result."""
        if not isinstance(result, dict | str):
            return []

        extractors = {
            "list_requests": self._extract_from_list_requests,
            "view_request": self._extract_from_view_request,
            "send_request": self._extract_from_send_request,
            "repeat_request": self._extract_from_repeat_request,
            "terminal_execute": self._extract_from_terminal,
            "browser_action": self._extract_from_browser,
        }

        extractor = extractors.get(tool_name)
        if extractor is None:
            return []

        try:
            return extractor(tool_args, result)
        except (KeyError, TypeError, ValueError) as e:
            logger.debug(f"Signal extraction failed for {tool_name}: {e}")
            return []

    def _extract_from_list_requests(
        self, args: dict[str, Any], result: Any
    ) -> list[AnomalyEvent]:
        """Extract signals from proxy list_requests results."""
        if not isinstance(result, dict):
            return []

        anomalies: list[AnomalyEvent] = []
        requests_list = result.get("requests", [])

        for req in requests_list:
            if not isinstance(req, dict):
                continue

            status = req.get("status_code") or (req.get("response", {}) or {}).get("statusCode")
            if isinstance(status, int) and status in _INTERESTING_STATUS_CODES:
                path = req.get("path", "")
                host = req.get("host", "")
                method = req.get("method", "")
                target = f"{method} {host}{path}" if host else f"{method} {path}"
                req_id = str(req.get("id", ""))

                anomalies.append(AnomalyEvent(
                    anomaly_type=AnomalyType.status_code,
                    source_tool="proxy",
                    description=f"HTTP {status} response on {target}",
                    target=target,
                    raw_data={"status_code": status, "method": method, "path": path, "host": host},
                    evidence_refs=[EvidenceRef(source="proxy", ref_id=req_id)] if req_id else [],
                ))

            # Timing anomalies
            roundtrip = (req.get("response", {}) or {}).get("roundtripTime")
            if isinstance(roundtrip, int | float) and roundtrip > _SLOW_RESPONSE_THRESHOLD_MS:
                path = req.get("path", "")
                req_id = str(req.get("id", ""))
                anomalies.append(AnomalyEvent(
                    anomaly_type=AnomalyType.timing,
                    source_tool="proxy",
                    description=f"Slow response ({roundtrip}ms) on {path}",
                    target=path,
                    raw_data={"roundtrip_ms": roundtrip, "path": path},
                    evidence_refs=[EvidenceRef(source="proxy", ref_id=req_id)] if req_id else [],
                ))

        return anomalies

    def _extract_from_view_request(
        self, args: dict[str, Any], result: Any
    ) -> list[AnomalyEvent]:
        """Extract signals from proxy view_request results."""
        if not isinstance(result, dict):
            return []

        anomalies: list[AnomalyEvent] = []
        req_id = args.get("request_id", "")
        body = result.get("body", "") or result.get("content", "")
        if not isinstance(body, str):
            body = str(body)

        anomalies.extend(self._check_error_leak(body, "proxy", req_id))
        anomalies.extend(self._check_injection_signals(body, "proxy", req_id))

        return anomalies

    def _extract_from_send_request(
        self, args: dict[str, Any], result: Any
    ) -> list[AnomalyEvent]:
        """Extract signals from proxy send_request results."""
        if not isinstance(result, dict):
            return []

        anomalies: list[AnomalyEvent] = []
        status = result.get("status_code")
        url = args.get("url", "")
        method = args.get("method", "")
        target = f"{method} {url}"
        req_id = str(result.get("id", ""))

        if isinstance(status, int) and status in _INTERESTING_STATUS_CODES:
            anomalies.append(AnomalyEvent(
                anomaly_type=AnomalyType.status_code,
                source_tool="proxy",
                description=f"HTTP {status} on crafted request to {target}",
                target=target,
                raw_data={"status_code": status, "url": url, "method": method},
                evidence_refs=[EvidenceRef(source="proxy", ref_id=req_id)] if req_id else [],
            ))

        body = result.get("body", "")
        if isinstance(body, str):
            anomalies.extend(self._check_error_leak(body, "proxy", req_id, target))
            anomalies.extend(self._check_injection_signals(body, "proxy", req_id, target))

        return anomalies

    def _extract_from_repeat_request(
        self, args: dict[str, Any], result: Any
    ) -> list[AnomalyEvent]:
        """Extract signals from proxy repeat_request results."""
        return self._extract_from_send_request(args, result)

    def _extract_from_terminal(
        self, args: dict[str, Any], result: Any
    ) -> list[AnomalyEvent]:
        """Extract signals from terminal_execute results."""
        if not isinstance(result, dict):
            return []

        anomalies: list[AnomalyEvent] = []
        content = result.get("content", "") or result.get("output", "")
        if not isinstance(content, str):
            return anomalies

        command = args.get("command", "")
        terminal_id = args.get("terminal_id", "default")
        ref_id = f"terminal:{terminal_id}"

        anomalies.extend(self._check_error_leak(content, "terminal", ref_id, command))

        # Check for interesting tool output patterns
        if _has_endpoint_discovery(content):
            anomalies.append(AnomalyEvent(
                anomaly_type=AnomalyType.unexpected_data,
                source_tool="terminal",
                description=f"Potential endpoint/URL discovery in command output: {command[:80]}",
                target=command[:120],
                raw_data={"command": command, "content_preview": content[:500]},
                evidence_refs=[EvidenceRef(source="terminal", ref_id=ref_id)],
            ))

        return anomalies

    def _extract_from_browser(
        self, args: dict[str, Any], result: Any
    ) -> list[AnomalyEvent]:
        """Extract signals from browser_action results."""
        if not isinstance(result, dict):
            return []

        anomalies: list[AnomalyEvent] = []
        action = args.get("action", "")
        url = result.get("url", "") or args.get("url", "")

        # Console logs may reveal errors
        console_logs = result.get("console_logs", [])
        if isinstance(console_logs, list):
            error_logs = [
                log for log in console_logs
                if isinstance(log, dict) and log.get("type") == "error"
            ]
            if error_logs:
                anomalies.append(AnomalyEvent(
                    anomaly_type=AnomalyType.error_leak,
                    source_tool="browser",
                    description=f"Browser console errors on {url} ({len(error_logs)} errors)",
                    target=url,
                    raw_data={"errors": error_logs[:5], "action": action},
                    evidence_refs=[EvidenceRef(source="browser", ref_id=f"console:{url}")],
                ))

        # Page source analysis
        source = result.get("source", "") or result.get("page_source", "")
        if isinstance(source, str) and source:
            anomalies.extend(self._check_injection_signals(source, "browser", f"page:{url}", url))

        return anomalies

    def _check_error_leak(
        self,
        content: str,
        source_tool: str,
        ref_id: str,
        target: str = "",
    ) -> list[AnomalyEvent]:
        """Check content for error information leakage patterns."""
        if not content:
            return []

        anomalies: list[AnomalyEvent] = []
        for pattern in _ERROR_LEAK_PATTERNS:
            match = pattern.search(content)
            if match:
                anomalies.append(AnomalyEvent(
                    anomaly_type=AnomalyType.error_leak,
                    source_tool=source_tool,
                    description=f"Error information leakage detected: {match.group()[:80]}",
                    target=target,
                    raw_data={"pattern": pattern.pattern, "match": match.group()[:200]},
                    evidence_refs=[EvidenceRef(source=source_tool, ref_id=ref_id)],
                ))
                break  # one error leak signal per content block is enough

        return anomalies

    def _check_injection_signals(
        self,
        content: str,
        source_tool: str,
        ref_id: str,
        target: str = "",
    ) -> list[AnomalyEvent]:
        """Check content for injection-related patterns."""
        if not content:
            return []

        anomalies: list[AnomalyEvent] = []
        for pattern in _INJECTION_SIGNAL_PATTERNS:
            match = pattern.search(content)
            if match:
                anomalies.append(AnomalyEvent(
                    anomaly_type=AnomalyType.injection_signal,
                    source_tool=source_tool,
                    description=f"Injection signal detected: {match.group()[:80]}",
                    target=target,
                    raw_data={"pattern": pattern.pattern, "match": match.group()[:200]},
                    evidence_refs=[EvidenceRef(source=source_tool, ref_id=ref_id)],
                ))
                break  # one injection signal per content block

        return anomalies


def _has_endpoint_discovery(content: str) -> bool:
    """Check if terminal output contains endpoint/URL patterns."""
    url_pattern = re.compile(
        r"https?://[^\s\"'<>]+/api/[^\s\"'<>]+", re.IGNORECASE
    )
    return bool(url_pattern.search(content))
