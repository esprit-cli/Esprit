"""VideoExporter — records a cinematic MP4 replay of a scan session."""

from __future__ import annotations

import base64
import json
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape


if TYPE_CHECKING:
    from esprit.telemetry.tracer import Tracer

logger = logging.getLogger(__name__)


class MissingDependencyError(RuntimeError):
    """Raised when an optional dependency (playwright, ffmpeg) is not available."""


class VideoExporter:
    """Exports a scan session as a cinematic MP4 video."""

    def __init__(self, tracer: Tracer) -> None:
        self.tracer = tracer
        self.template_dir = Path(__file__).parent / "templates"
        self.env = Environment(
            loader=FileSystemLoader(str(self.template_dir)),
            autoescape=select_autoescape(["html", "jinja"]),
        )

    # ─── Public API ───────────────────────────────────────────────────────────

    def export_video(
        self,
        output_path: str | Path,
        speed: float = 10.0,
        resolution: tuple[int, int] = (1920, 1080),
    ) -> Path:
        """Render the scan replay and save as MP4."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="esprit_video_") as tmp_str:
            tmp_dir = Path(tmp_str)
            events = self._build_events()
            html_path = self._render_template(events, speed, resolution, tmp_dir)
            webm_path = self._record_with_playwright(html_path, tmp_dir, resolution)
            self._convert_to_mp4(webm_path, output_path)

        logger.info("Video exported to %s", output_path)
        return output_path

    # ─── Internal pipeline ────────────────────────────────────────────────────

    def _build_events(self) -> list[dict[str, Any]]:
        """Collect all tracer events into a time-sorted list."""
        t = self.tracer
        events: list[dict[str, Any]] = []

        for msg in t.chat_messages:
            events.append({"type": "message", "timestamp": msg["timestamp"], "data": msg})

        for exec_id, tool in t.tool_executions.items():
            events.append({
                "type": "tool_start",
                "timestamp": tool["started_at"],
                "data": {
                    "execution_id": exec_id,
                    "tool_name": tool.get("tool_name", ""),
                    "agent_id": tool.get("agent_id", ""),
                    "args": {k: v for k, v in tool.get("args", {}).items() if k != "screenshot"},
                },
            })
            if tool.get("completed_at"):
                end_data = {
                    "execution_id": exec_id,
                    "tool_name": tool.get("tool_name", ""),
                    "agent_id": tool.get("agent_id", ""),
                    "status": tool.get("status", ""),
                }
                end_data.update(self._summarize_tool_result(tool))
                events.append({
                    "type": "tool_end",
                    "timestamp": tool["completed_at"],
                    "data": end_data,
                })

        for vuln in t.vulnerability_reports:
            events.append({"type": "vulnerability", "timestamp": vuln["timestamp"], "data": vuln})

        for agent_id, agent in t.agents.items():
            events.append({
                "type": "agent_spawn",
                "timestamp": agent.get("created_at", t.start_time),
                "data": {
                    "agent_id": agent_id,
                    "name": agent.get("name", agent_id),
                    "task": agent.get("task", ""),
                    "parent_id": agent.get("parent_id"),
                },
            })

        events.sort(key=lambda e: e["timestamp"])
        return events

    def _summarize_text(
        self,
        value: Any,
        *,
        max_lines: int = 8,
        max_chars: int = 520,
    ) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, default=str)
            except Exception:
                text = str(value)

        lines = [line.rstrip() for line in text.replace("\r\n", "\n").split("\n")]
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            non_empty = lines[:1]
        clipped = non_empty[:max_lines]
        combined = "\n".join(clipped).strip()
        if not combined:
            return None
        if len(combined) > max_chars:
            return combined[: max_chars - 1] + "…"
        return combined

    def _normalize_screenshot_data(self, screenshot: Any) -> str | None:
        if not isinstance(screenshot, str):
            return None
        payload = screenshot.strip()
        if not payload or payload == "[rendered]":
            return None
        if payload.startswith("data:image/"):
            return payload
        if payload.startswith(("iVBORw0KGgo", "/9j/")):
            return f"data:image/png;base64,{payload}"
        return None

    def _summarize_list_requests(self, result: dict[str, Any]) -> str | None:
        requests = result.get("requests")
        if not isinstance(requests, list) or not requests:
            return self._summarize_text(result.get("error"), max_lines=2, max_chars=220)

        lines: list[str] = []
        for req in requests[:6]:
            if not isinstance(req, dict):
                continue
            method = str(req.get("method", "GET"))
            host = str(req.get("host", ""))
            path = str(req.get("path", ""))
            response = req.get("response", {})
            code = ""
            if isinstance(response, dict):
                code_raw = response.get("status") or response.get("statusCode")
                if code_raw is not None:
                    code = f" -> {code_raw}"
            target = f"{host}{path}" if host else path
            lines.append(f"{method} {target}{code}".strip())
        return self._summarize_text("\n".join(lines), max_lines=6, max_chars=420)

    def _summarize_tool_result(self, tool: dict[str, Any]) -> dict[str, Any]:
        summary: dict[str, Any] = {}
        tool_name = str(tool.get("tool_name", ""))
        result = tool.get("result")
        if not isinstance(result, dict):
            preview = self._summarize_text(result, max_lines=4, max_chars=280)
            if preview:
                summary["response_preview"] = preview
            return summary

        if tool_name == "browser_action":
            screenshot_data = self._normalize_screenshot_data(result.get("screenshot"))
            if screenshot_data:
                summary["browser_screenshot"] = screenshot_data
            browser_url = result.get("url")
            if isinstance(browser_url, str) and browser_url.strip():
                summary["browser_url"] = browser_url.strip()
            browser_title = result.get("title")
            if isinstance(browser_title, str) and browser_title.strip():
                summary["browser_title"] = browser_title.strip()
            viewport = result.get("viewport")
            if (
                isinstance(viewport, dict)
                and isinstance(viewport.get("width"), (int, float))
                and isinstance(viewport.get("height"), (int, float))
            ):
                summary["browser_viewport"] = {
                    "width": int(viewport["width"]),
                    "height": int(viewport["height"]),
                }
            preview = self._summarize_text(result.get("message"), max_lines=2, max_chars=180)
            if preview:
                summary["response_preview"] = preview
            return summary

        if tool_name == "terminal_execute":
            preview = self._summarize_text(result.get("content"), max_lines=8, max_chars=520)
            if preview:
                summary["response_preview"] = preview
            code = result.get("exit_code")
            if isinstance(code, int):
                summary["exit_code"] = code
            return summary

        if tool_name == "python_action":
            stdout = self._summarize_text(result.get("stdout"), max_lines=6, max_chars=380)
            stderr = self._summarize_text(result.get("stderr"), max_lines=4, max_chars=240)
            parts: list[str] = []
            if stdout:
                parts.append(stdout)
            if stderr:
                parts.append(f"stderr: {stderr}")
            if not parts:
                generic = self._summarize_text(result.get("message"), max_lines=2, max_chars=160)
                if generic:
                    parts.append(generic)
            if parts:
                summary["response_preview"] = self._summarize_text("\n".join(parts), max_lines=10, max_chars=560)
            return summary

        if tool_name == "list_requests":
            preview = self._summarize_list_requests(result)
            if preview:
                summary["response_preview"] = preview
            total = result.get("total_count")
            if isinstance(total, int):
                summary["total_count"] = total
            return summary

        if tool_name == "view_request":
            preview = self._summarize_text(result.get("content"), max_lines=8, max_chars=520)
            if preview:
                summary["response_preview"] = preview
            return summary

        generic_preview = self._summarize_text(result.get("message"), max_lines=3, max_chars=220)
        if generic_preview:
            summary["response_preview"] = generic_preview
        return summary

    def _render_template(
        self,
        events: list[dict[str, Any]],
        speed: float,
        resolution: tuple[int, int],
        tmp_dir: Path,
    ) -> Path:
        """Render video_replay.html.jinja -> a temp HTML file."""
        t = self.tracer
        template = self.env.get_template("video_replay.html.jinja")

        vulns = t.vulnerability_reports
        severity_counts = {
            sev: len([v for v in vulns if v.get("severity") == sev])
            for sev in ("critical", "high", "medium", "low")
        }

        duration_s = t._calculate_duration()
        minutes = int(duration_s // 60)
        seconds = int(duration_s % 60)

        scan_cfg = t.scan_config or {}
        targets = scan_cfg.get("targets", [])
        target_display = targets[0]["original"] if targets else "unknown"

        context = {
            "run_id": t.run_id,
            "run_name": t.run_name or t.run_id,
            "target": target_display,
            "duration": f"{minutes}m {seconds}s",
            "tool_count": len(t.tool_executions),
            "speed": speed,
            "width": resolution[0],
            "height": resolution[1],
            "severity_counts": severity_counts,
            "events_b64": base64.b64encode(
                json.dumps(events, default=str, ensure_ascii=False).encode("utf-8")
            ).decode("ascii"),
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        }

        html = template.render(context)
        html_path = tmp_dir / "replay.html"
        html_path.write_text(html, encoding="utf-8")
        return html_path

    def _record_with_playwright(
        self,
        html_path: Path,
        output_dir: Path,
        resolution: tuple[int, int],
    ) -> Path:
        """Open the HTML in headless Chromium, record video, return .webm path."""
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeout
            from playwright.sync_api import sync_playwright
        except ImportError as e:
            raise MissingDependencyError(
                "playwright is required for video export.\n"
                "Install with: pip install 'esprit-cli[video]' && "
                "python -m playwright install chromium"
            ) from e

        width, height = resolution
        video_dir = output_dir / "video_raw"
        video_dir.mkdir(exist_ok=True)
        timeout_ms = 120_000

        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True,
                args=["--autoplay-policy=no-user-gesture-required"],
            )
            ctx = browser.new_context(
                viewport={"width": width, "height": height},
                record_video_dir=str(video_dir),
                record_video_size={"width": width, "height": height},
            )
            page = ctx.new_page()
            page.goto(f"file://{html_path.resolve()}")

            try:
                page.wait_for_function(
                    "document.title === 'ESPRIT_DONE'",
                    timeout=timeout_ms,
                )
            except PlaywrightTimeout:
                logger.warning("Video replay timed out waiting for ESPRIT_DONE — proceeding anyway")

            page.wait_for_timeout(500)
            ctx.close()
            webm_path = Path(page.video.path())
            browser.close()

        return webm_path

    def _convert_to_mp4(self, webm_path: Path, output_path: Path) -> Path:
        """Convert .webm -> .mp4 with FFmpeg."""
        if not shutil.which("ffmpeg"):
            raise MissingDependencyError(
                "ffmpeg is required for video export.\n"
                "Install with: brew install ffmpeg"
            )

        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(webm_path),
                "-c:v", "libx264",
                "-preset", "fast",
                "-crf", "18",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                str(output_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg conversion failed:\n{result.stderr[-1000:]}")

        return output_path
