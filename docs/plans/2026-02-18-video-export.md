# Video Export Feature Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let users export a cinematic 1080p MP4 replay of any scan session — smooth, clean, minimalistic macOS-window style — triggerable from the TUI ExportScreen or via `esprit report --video`.

**Architecture:** A `VideoExporter` class serializes all tracer events into a self-contained Jinja2 HTML template, Playwright records it as a `.webm` while the page animates at configurable speed, then FFmpeg converts it to `.mp4`. No glitch effects — clean fade-in intro, smooth animations, spring-physics window entry, shockwave on vuln cards, fade-to-black outro.

**Tech Stack:** Python, Jinja2 (already used), Playwright (already optional dep), FFmpeg (system, checked at runtime), Web Audio API (in-browser JS, no extra deps)

---

### Task 1: Add `video` optional dep + wire `playwright` to it

**Files:**
- Modify: `pyproject.toml:84-88`

**Step 1: Edit pyproject.toml**

Add `video` to the extras section. Note: `playwright` is already declared as an optional dep under `sandbox`; we just add a new alias.

```toml
# In [tool.poetry.extras] section, add:
video = ["playwright"]
```

The `playwright` dep declaration already exists at line 71 — no need to add it again.

**Step 2: Verify the extras section looks like this after edit**

```toml
[tool.poetry.extras]
vertex = ["google-cloud-aiplatform"]
sandbox = ["fastapi", "uvicorn", "ipython", "openhands-aci", "playwright", "gql", "pyte", "libtmux", "numpydoc"]
gui = ["fastapi", "uvicorn", "websockets"]
enhanced-preview = ["textual-image"]
video = ["playwright"]
```

**Step 3: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add video optional dep extra"
```

---

### Task 2: Create `VideoExporter` class

**Files:**
- Create: `esprit/reporting/video_exporter.py`
- Test: `tests/reporting/test_video_exporter.py`

**Step 1: Write the failing tests**

```python
# tests/reporting/test_video_exporter.py
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from esprit.reporting.video_exporter import VideoExporter, MissingDependencyError


def make_tracer():
    tracer = MagicMock()
    tracer.run_id = "test-run-001"
    tracer.run_name = "test-scan"
    tracer.start_time = "2026-02-18T10:00:00+00:00"
    tracer.end_time = "2026-02-18T10:04:31+00:00"
    tracer.chat_messages = [
        {"timestamp": "2026-02-18T10:00:01+00:00", "role": "assistant", "content": "Starting scan"},
    ]
    tracer.tool_executions = {
        1: {
            "tool_name": "terminal",
            "agent_id": "agent-1",
            "started_at": "2026-02-18T10:00:02+00:00",
            "completed_at": "2026-02-18T10:00:03+00:00",
            "status": "success",
            "args": {"command": "nmap -sV target.com"},
            "result": "80/tcp open http",
        }
    }
    tracer.vulnerability_reports = [
        {
            "id": "vuln-001",
            "title": "SQL Injection",
            "severity": "critical",
            "cvss": 9.8,
            "timestamp": "2026-02-18T10:02:00+00:00",
            "target": "https://target.com",
            "endpoint": "/api/users",
        }
    ]
    tracer.agents = {
        "agent-1": {
            "name": "root-agent",
            "task": "Orchestrating scan",
            "status": "done",
            "created_at": "2026-02-18T10:00:00+00:00",
            "parent_id": None,
        }
    }
    tracer.scan_config = {
        "targets": [{"original": "target.com"}],
        "run_name": "test-scan",
    }
    tracer.run_metadata = {"status": "complete"}
    tracer._calculate_duration = MagicMock(return_value=271.0)
    return tracer


def test_build_events_includes_messages_tools_vulns():
    exporter = VideoExporter(make_tracer())
    events = exporter._build_events()
    types = [e["type"] for e in events]
    assert "message" in types
    assert "tool_start" in types
    assert "vulnerability" in types


def test_build_events_sorted_by_timestamp():
    exporter = VideoExporter(make_tracer())
    events = exporter._build_events()
    timestamps = [e["timestamp"] for e in events]
    assert timestamps == sorted(timestamps)


def test_render_template_produces_html(tmp_path):
    exporter = VideoExporter(make_tracer())
    events = exporter._build_events()
    html_path = exporter._render_template(events, speed=10.0, resolution=(1920, 1080), tmp_dir=tmp_path)
    assert html_path.exists()
    content = html_path.read_text()
    assert "ESPRIT_DONE" in content
    assert "ESPRIT_LOADING" in content
    assert "test-scan" in content


def test_render_template_embeds_speed(tmp_path):
    exporter = VideoExporter(make_tracer())
    events = exporter._build_events()
    html_path = exporter._render_template(events, speed=20.0, resolution=(1920, 1080), tmp_dir=tmp_path)
    content = html_path.read_text()
    assert "20" in content  # speed value embedded


def test_missing_playwright_raises_clear_error(tmp_path):
    exporter = VideoExporter(make_tracer())
    with patch.dict("sys.modules", {"playwright": None, "playwright.sync_api": None}):
        with pytest.raises(MissingDependencyError, match="playwright"):
            exporter._record_with_playwright(Path("/fake.html"), tmp_path, (1920, 1080))


def test_missing_ffmpeg_raises_clear_error(tmp_path):
    exporter = VideoExporter(make_tracer())
    fake_webm = tmp_path / "fake.webm"
    fake_webm.write_bytes(b"")
    with patch("shutil.which", return_value=None):
        with pytest.raises(MissingDependencyError, match="ffmpeg"):
            exporter._convert_to_mp4(fake_webm, tmp_path / "out.mp4")
```

**Step 2: Run tests to confirm they fail**

```bash
cd /Users/shauryagupta/Downloads/Esprit
python -m pytest tests/reporting/test_video_exporter.py -v --no-cov 2>&1 | head -30
```
Expected: `ModuleNotFoundError: No module named 'esprit.reporting.video_exporter'`

**Step 3: Create `esprit/reporting/video_exporter.py`**

```python
"""VideoExporter — records a cinematic MP4 replay of a scan session."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from esprit.utils.resource_paths import get_esprit_resource_path


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
            loader=FileSystemLoader(self.template_dir),
            autoescape=select_autoescape(["html"]),
        )

    # ─── Public API ───────────────────────────────────────────────────────────

    def export_video(
        self,
        output_path: str | Path,
        speed: float = 10.0,
        resolution: tuple[int, int] = (1920, 1080),
    ) -> Path:
        """Render the scan replay and save as MP4.

        Args:
            output_path: Destination .mp4 path.
            speed: Playback speed multiplier (10 = 10x faster than real-time).
            resolution: (width, height) in pixels. Default 1920x1080.

        Returns:
            Path to the created MP4 file.

        Raises:
            MissingDependencyError: If playwright or ffmpeg are not installed.
        """
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
                events.append({
                    "type": "tool_end",
                    "timestamp": tool["completed_at"],
                    "data": {"execution_id": exec_id, "status": tool.get("status", "")},
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

    def _render_template(
        self,
        events: list[dict[str, Any]],
        speed: float,
        resolution: tuple[int, int],
        tmp_dir: Path,
    ) -> Path:
        """Render video_replay.html.jinja → a temp HTML file."""
        t = self.tracer
        template = self.env.get_template("video_replay.html.jinja")

        # Severity summary for the outro card
        vulns = t.vulnerability_reports
        severity_counts = {
            sev: len([v for v in vulns if v.get("severity") == sev])
            for sev in ("critical", "high", "medium", "low")
        }

        duration_s = t._calculate_duration()
        minutes = int(duration_s // 60)
        seconds = int(duration_s % 60)

        # Scan config display values
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
            "events_json": json.dumps(events, default=str),
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

        # Estimate timeout: last event offset / speed + 10s buffer
        # We wait for document.title to become 'ESPRIT_DONE'
        # The template sets this after the outro finishes.
        # We give it a generous 120s hard timeout.
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
            except Exception:  # noqa: BLE001
                logger.warning("Video replay timed out waiting for ESPRIT_DONE — proceeding anyway")

            # Extra buffer so the black frame is captured
            page.wait_for_timeout(500)
            webm_path = Path(page.video.path())
            ctx.close()
            browser.close()

        return webm_path

    def _convert_to_mp4(self, webm_path: Path, output_path: Path) -> Path:
        """Convert .webm → .mp4 with FFmpeg."""
        if not shutil.which("ffmpeg"):
            raise MissingDependencyError(
                "ffmpeg is required for video export.\n"
                "Install with: brew install ffmpeg"
            )

        result = subprocess.run(  # noqa: S603
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
        )

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg conversion failed:\n{result.stderr[-1000:]}")

        return output_path
```

**Step 4: Create the tests directory if needed**

```bash
mkdir -p /Users/shauryagupta/Downloads/Esprit/tests/reporting
touch /Users/shauryagupta/Downloads/Esprit/tests/reporting/__init__.py
```

**Step 5: Run tests**

```bash
python -m pytest tests/reporting/test_video_exporter.py -v --no-cov
```
Expected: All 7 tests pass. The `_record_with_playwright` and `_convert_to_mp4` tests patch dependencies so they don't need real Playwright/FFmpeg.

**Step 6: Commit**

```bash
git add esprit/reporting/video_exporter.py tests/reporting/test_video_exporter.py tests/reporting/__init__.py
git commit -m "feat: add VideoExporter class with Playwright + FFmpeg pipeline"
```

---

### Task 3: Create `video_replay.html.jinja` template

**Files:**
- Create: `esprit/reporting/templates/video_replay.html.jinja`

This is the cinematic template. No glitch effects. Smooth, clean, minimalistic.

**Step 1: Create the template**

The template is ~600 lines of HTML/CSS/JS. Key design rules baked in:
- **No glitch animations** — logo fades in cleanly
- **Intro**: black screen → "ESPRIT" fades in → subtitle → window drops with spring physics
- **macOS window**: `border-radius: 12px`, traffic lights, subtle top reflection
- **Background**: slow animated dark radial gradient + noise texture overlay
- **Vuln cards**: spring slam-in + shockwave ring, no screen flash beyond a subtle 2-frame dimming
- **Outro**: dim overlay → summary card rises → fade to black → Esprit logo
- **Audio**: Web Audio API ambient drone, tool ping, vuln thud, resolution chord
- **Completion signal**: `document.title = 'ESPRIT_DONE'` after outro finishes

Write the template to `esprit/reporting/templates/video_replay.html.jinja` using the following structure:

```
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>ESPRIT_LOADING</title>
  <style>
    /* CSS vars matching the Web GUI: --bg, --accent #22d3ee, maroon borders etc */
    /* Body: 1920x1080, overflow hidden, flex center */
    /* .bg-layer: dark radial gradient */
    /* .bg-noise: subtle noise texture (SVG data URI) */
    /* .bg-glow: slow pulsing cyan radial glow */
    /* #intro: full-screen overlay, black, flex column center */
    /* .intro-logo: font-size 64px, letter-spacing 18px, accent color, fadeIn only */
    /* .intro-sub: 12px uppercase tracking */
    /* .window-wrap: opacity 0 → 1, translateY(-50px) → 0, spring cubic-bezier */
    /* .macos-window: 1780x970, border-radius 12px, dark bg */
    /* .titlebar, .header, .context-bar, .scan-config */
    /* #main: 3-column grid: 220px / 1fr / 280px */
    /* .agent-item: fade+slide in from left */
    /* .term-line: fade+slide in from left */
    /* .vuln-card: slam from right with spring bounce + shockwave */
    /* #outro: fixed overlay, transitions from transparent → dim → black */
    /* .outro-card: rises from bottom, severity breakdown + Esprit logo */
  </style>
</head>
<body>
  <!-- bg layers -->
  <!-- #flash (subtle, 2-frame white flash for critical vulns only) -->
  <!-- #intro -->
  <!-- #outro with .outro-card -->
  <!-- .scanlines overlay -->
  <!-- .window-wrap > .macos-window -->
    <!-- .titlebar (traffic lights + run name) -->
    <!-- #header (logo + status badge + stat cards) -->
    <!-- .context-bar > .context-bar-fill -->
    <!-- .scan-config (target, mode, model, run) -->
    <!-- #main (3-panel grid) -->
      <!-- agents panel -->
      <!-- terminal panel (chrome dots + #tf feed) -->
      <!-- vulns panel -->
    <!-- .status-bar -->

  <script>
    // Audio: ping(), thud(), drone(), chord() via Web Audio API
    // State: agents, tools, tokens, cost, vulnCount, scanStart
    // Helpers: flash(), animN(), addLine(), addAgent(), doneAgent(), addVuln(), updStats()
    // Events array from Jinja: const EVENTS = {{ events_json }};
    // const SPEED = {{ speed }};
    // Schedule: events.forEach(ev => setTimeout(ev.fn, (ev.realOffsetMs) / SPEED))
    // Outro at end, then document.title = 'ESPRIT_DONE'
  </script>
</body>
</html>
```

The Jinja template uses `{{ events_json }}`, `{{ speed }}`, `{{ run_name }}`, `{{ target }}`, `{{ duration }}`, `{{ tool_count }}`, `{{ severity_counts.critical }}` etc.

The JS replay engine:
1. On load: record `t0 = events[0].timestamp` (ISO string → Date)
2. For each event: compute `offsetMs = (new Date(ev.timestamp) - t0)`, schedule with `setTimeout(fn, offsetMs / SPEED)`
3. Each event type maps to a DOM action:
   - `agent_spawn` → `addAgent()`
   - `tool_start` → `addLine()` + `updStats()`
   - `vulnerability` → `addVuln()`
   - `message` → `addLine()`
4. After last event + 3s: run outro sequence
5. After outro: `document.title = 'ESPRIT_DONE'`

**Step 2: Smoke-test the template renders**

```bash
python3 -c "
from esprit.reporting.video_exporter import VideoExporter
from unittest.mock import MagicMock
import tempfile, pathlib

t = MagicMock()
t.run_id='test'; t.run_name='test-scan'; t.start_time='2026-02-18T10:00:00+00:00'
t.end_time='2026-02-18T10:04:31+00:00'; t.chat_messages=[]; t.tool_executions={}
t.vulnerability_reports=[]; t.agents={}; t.scan_config={'targets':[{'original':'target.com'}]}
t.run_metadata={'status':'complete'}; t._calculate_duration=MagicMock(return_value=271.0)

e = VideoExporter(t)
with tempfile.TemporaryDirectory() as d:
    path = e._render_template([], 10.0, (1920,1080), pathlib.Path(d))
    content = path.read_text()
    assert 'ESPRIT_DONE' in content
    assert 'test-scan' in content
    print('Template OK, size:', len(content))
"
```
Expected: `Template OK, size: <N>`

**Step 3: Commit**

```bash
git add esprit/reporting/templates/video_replay.html.jinja
git commit -m "feat: add cinematic video_replay.html.jinja template"
```

---

### Task 4: Wire `VideoExportScreen` into the TUI

**Files:**
- Modify: `esprit/interface/tui.py`
- Modify: `esprit/interface/assets/tui_styles.tcss`

**Step 1: Add `VideoExportScreen` class to `tui.py` (after `ExportScreen`, before `EspritTUIApp`)**

Add at line ~908 (between `ExportScreen.on_button_pressed` and `class EspritTUIApp`):

```python
class VideoExportScreen(ModalScreen):  # type: ignore[misc]
    """Modal screen for configuring and triggering video export."""

    def compose(self) -> ComposeResult:
        yield Grid(
            Label("Export as Video (MP4)", id="video_export_title"),
            Label("Speed", id="video_speed_label"),
            Horizontal(
                Button("5×", id="speed_5", classes="speed_btn"),
                Button("10×", id="speed_10", classes="speed_btn active_speed"),
                Button("20×", id="speed_20", classes="speed_btn"),
                Button("50×", id="speed_50", classes="speed_btn"),
                id="speed_buttons",
            ),
            Label("Resolution", id="video_res_label"),
            Horizontal(
                Button("1080p", id="res_1080", classes="res_btn active_res"),
                Button("720p", id="res_720", classes="res_btn"),
                id="res_buttons",
            ),
            Button("Render Video", id="render_video", variant="primary"),
            Button("Cancel", id="cancel_video"),
            id="video_export_dialog",
        )

    def on_mount(self) -> None:
        self._speed = 10.0
        self._resolution = (1920, 1080)
        try:
            self.query_one("#render_video", Button).focus()
        except (ValueError, Exception):
            pass

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.app.pop_screen()
            event.prevent_default()

    def on_button_pressed(self, event: Button.Pressed) -> None:  # noqa: PLR0912
        btn_id = event.button.id

        # Speed selection
        if btn_id and btn_id.startswith("speed_"):
            speed_map = {"speed_5": 5.0, "speed_10": 10.0, "speed_20": 20.0, "speed_50": 50.0}
            self._speed = speed_map.get(btn_id, 10.0)
            for b in self.query(".speed_btn"):
                b.remove_class("active_speed")
            event.button.add_class("active_speed")
            return

        # Resolution selection
        if btn_id == "res_1080":
            self._resolution = (1920, 1080)
            for b in self.query(".res_btn"):
                b.remove_class("active_res")
            event.button.add_class("active_res")
            return
        if btn_id == "res_720":
            self._resolution = (1280, 720)
            for b in self.query(".res_btn"):
                b.remove_class("active_res")
            event.button.add_class("active_res")
            return

        if btn_id == "cancel_video":
            self.app.pop_screen()
            return

        if btn_id == "render_video" and isinstance(self.app, EspritTUIApp):
            self.app.pop_screen()
            self.app._do_export_video(self._speed, self._resolution)
```

**Step 2: Add `"export_video"` to `ExportScreen.EXPORT_FORMATS`**

In `ExportScreen` at line ~866, add:
```python
("export_video", "MP4 Video", "Cinematic scan replay"),
```

**Step 3: Handle `export_video` in `ExportScreen.on_button_pressed`**

In `ExportScreen.on_button_pressed`, before `self.app._do_export_vulnerabilities(fmt)`, add:
```python
if fmt == "video":
    if isinstance(self.app, EspritTUIApp):
        self.app.pop_screen()
        self.app.push_screen(VideoExportScreen())
    return
```

**Step 4: Add `_do_export_video` method to `EspritTUIApp`**

After `_do_export_vulnerabilities` at line ~2712:
```python
def _do_export_video(self, speed: float, resolution: tuple[int, int]) -> None:
    """Trigger video export in a background thread."""
    import threading

    def _run() -> None:
        try:
            from esprit.reporting.video_exporter import VideoExporter, MissingDependencyError
            exporter = VideoExporter(self.tracer)
            out_path = exporter._get_output_dir() / "replay.mp4"
            # Show a progress notification
            self.call_from_thread(
                self.notify,
                "Rendering video… this may take a minute.",
                title="Video Export",
                timeout=60,
            )
            out = exporter.export_video(out_path, speed=speed, resolution=resolution)
            self.call_from_thread(
                self.notify,
                f"Saved to {out}",
                title="Video Export Complete",
                timeout=8,
            )
        except MissingDependencyError as e:
            self.call_from_thread(
                self.notify,
                str(e),
                title="Missing Dependency",
                severity="error",
                timeout=10,
            )
        except Exception as e:  # noqa: BLE001
            logging.error("Video export failed: %s", e)
            self.call_from_thread(
                self.notify,
                f"Export failed: {e}",
                title="Error",
                severity="error",
                timeout=8,
            )

    threading.Thread(target=_run, daemon=True).start()
```

Note: `VideoExporter` needs a `_get_output_dir()` method — add it:
```python
def _get_output_dir(self) -> Path:
    return self.tracer.get_run_dir()
```

**Step 5: Add styles to `tui_styles.tcss`**

Append to the end of `esprit/interface/assets/tui_styles.tcss`:

```css
/* ── VIDEO EXPORT MODAL ── */
#video_export_dialog {
    grid-size: 2;
    grid-rows: auto auto auto auto auto auto;
    grid-columns: 1fr;
    width: 50;
    height: auto;
    background: #110404;
    border: solid #2f0f0f;
    padding: 2 3;
    align: center middle;
}

#video_export_title {
    column-span: 2;
    text-align: center;
    color: #22d3ee;
    text-style: bold;
    padding-bottom: 1;
}

#video_speed_label, #video_res_label {
    color: #6b4444;
    margin-top: 1;
}

#speed_buttons, #res_buttons {
    height: auto;
    gap: 1;
}

.speed_btn, .res_btn {
    background: #160606;
    border: solid #2f0f0f;
    color: #947575;
    min-width: 6;
}

.active_speed, .active_res {
    background: #0a3d47;
    border: solid #22d3ee;
    color: #22d3ee;
}

#render_video {
    margin-top: 2;
    background: #0a3d47;
    border: solid #22d3ee;
    color: #22d3ee;
}

#cancel_video {
    background: #160606;
    border: solid #2f0f0f;
    color: #6b4444;
}
```

**Step 6: Run the TUI (manual test)**

```bash
cd /Users/shauryagupta/Downloads/Esprit
python -m esprit scan http://example.com  # or any target
# Press 'e' → verify "MP4 Video  Cinematic scan replay" appears as last option
# Select it → verify VideoExportScreen modal appears with speed + resolution buttons
```

**Step 7: Commit**

```bash
git add esprit/interface/tui.py esprit/interface/assets/tui_styles.tcss
git commit -m "feat: add VideoExportScreen modal to TUI export menu"
```

---

### Task 5: Add `esprit report --video` CLI command

**Files:**
- Modify: `esprit/interface/main.py:778-790` (the `report` subparser)

**Step 1: Add `--video` flag to the `report` subparser**

Find the `report_parser` block at line ~778 and add:
```python
report_parser.add_argument(
    "--video",
    action="store_true",
    help="Export scan replay as MP4 video",
)
report_parser.add_argument(
    "--speed",
    type=float,
    default=10.0,
    help="Video playback speed multiplier (default: 10)",
)
report_parser.add_argument(
    "--resolution",
    choices=["1080p", "720p"],
    default="1080p",
    help="Video resolution (default: 1080p)",
)
```

**Step 2: Handle `report` command in `parse_arguments`**

After the `uninstall` handler (line ~838), add:
```python
if args.command == "report":
    _cmd_report(args)
    sys.exit(0)
```

**Step 3: Add `_cmd_report` function to `main.py`**

Add after `cmd_uninstall` (line ~700):

```python
def _cmd_report(args: argparse.Namespace) -> None:
    """Handle the `esprit report` subcommand."""
    from pathlib import Path

    console = Console()
    run_dir = Path("esprit_runs") / args.run_id
    if not run_dir.exists():
        # Try treating run_id as a direct path
        run_dir = Path(args.run_id)
    if not run_dir.exists():
        console.print(f"[red]Run directory not found:[/] {args.run_id}")
        console.print("[dim]Usage: esprit report <run-id> --html --video[/]")
        sys.exit(1)

    # Load the tracer from checkpoint
    from esprit.telemetry.tracer import Tracer
    tracer = Tracer.load_from_dir(run_dir)
    if tracer is None:
        console.print(f"[red]Could not load scan data from:[/] {run_dir}")
        sys.exit(1)

    from esprit.reporting.exporter import ReportExporter
    exporter = ReportExporter(tracer)

    if args.html:
        out = exporter.generate_html_report(run_dir / "report.html")
        console.print(f"[green]HTML report:[/] {out}")

    if args.timelapse:
        out = exporter.generate_timelapse(run_dir / "timelapse.html")
        console.print(f"[green]Timelapse:[/] {out}")

    if args.video:
        resolution_map = {"1080p": (1920, 1080), "720p": (1280, 720)}
        resolution = resolution_map[args.resolution]
        output = Path(args.output) if args.output else run_dir / "replay.mp4"
        try:
            from esprit.reporting.video_exporter import VideoExporter, MissingDependencyError
            video_exporter = VideoExporter(tracer)
            with console.status("[cyan]Rendering video…"):
                out = video_exporter.export_video(output, speed=args.speed, resolution=resolution)
            console.print(f"[green]Video:[/] {out}")
        except MissingDependencyError as e:
            console.print(f"[red]Missing dependency:[/] {e}")
            sys.exit(1)
```

**Step 4: Check `Tracer.load_from_dir` exists**

```bash
grep -n "load_from_dir\|from_dir\|load_checkpoint" /Users/shauryagupta/Downloads/Esprit/esprit/telemetry/tracer.py
```

If it doesn't exist, you need to add a minimal loader. Look at how the tracer saves checkpoints (grep for `_save_lock`, `json.dump`) and implement a matching `load_from_dir` classmethod on `Tracer`.

**Step 5: Manual test CLI**

```bash
esprit report <an-existing-run-id> --video --speed 10
```
Expected: progress indicator, then `Video: esprit_runs/<run-id>/replay.mp4`

**Step 6: Commit**

```bash
git add esprit/interface/main.py esprit/telemetry/tracer.py  # if tracer modified
git commit -m "feat: add esprit report --video CLI flag"
```

---

### Task 6: Include template in package distribution

**Files:**
- Modify: `pyproject.toml:35-43` (the `include` section)

**Step 1: Add `video_replay.html.jinja` to the package include list**

The `include` block already has `esprit/**/*.xml` and `esprit/**/*.tcss`. Check if Jinja templates from `esprit/reporting/templates/` are already included:

```bash
grep -n "jinja\|templates\|reporting" /Users/shauryagupta/Downloads/Esprit/pyproject.toml
```

If the reporting templates aren't covered, add:
```toml
"esprit/reporting/templates/**/*",
```
to the `include` list. The existing `esprit/agents/**/*.jinja` pattern only covers agents — reporting templates need their own entry.

**Step 2: Commit**

```bash
git add pyproject.toml
git commit -m "chore: include reporting templates in package distribution"
```

---

### Task 7: End-to-end smoke test

**Step 1: Generate a mock scan run**

```bash
python3 << 'EOF'
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta

# Create a fake run directory
run_dir = Path("esprit_runs/smoke-test-video-001")
run_dir.mkdir(parents=True, exist_ok=True)

base = datetime(2026, 2, 18, 10, 0, 0, tzinfo=timezone.utc)

checkpoint = {
    "run_id": "smoke-test-video-001",
    "run_name": "smoke-test-video-001",
    "start_time": base.isoformat(),
    "end_time": (base + timedelta(minutes=4, seconds=31)).isoformat(),
    "agents": {
        "agent-1": {
            "name": "root-agent",
            "task": "Orchestrating scan of api.example.com",
            "status": "done",
            "created_at": base.isoformat(),
            "parent_id": None,
        },
        "agent-2": {
            "name": "recon-agent",
            "task": "Port scanning",
            "status": "done",
            "created_at": (base + timedelta(seconds=5)).isoformat(),
            "parent_id": "agent-1",
        },
    },
    "tool_executions": {
        "1": {
            "tool_name": "terminal",
            "agent_id": "agent-1",
            "started_at": (base + timedelta(seconds=3)).isoformat(),
            "completed_at": (base + timedelta(seconds=8)).isoformat(),
            "status": "success",
            "args": {"command": "nmap -sV api.example.com"},
            "result": "80/tcp open http",
        },
        "2": {
            "tool_name": "web_search",
            "agent_id": "agent-2",
            "started_at": (base + timedelta(seconds=15)).isoformat(),
            "completed_at": (base + timedelta(seconds=20)).isoformat(),
            "status": "success",
            "args": {"query": "CVE Express.js 4.18.2"},
            "result": "Found 3 relevant CVEs",
        },
    },
    "chat_messages": [
        {
            "timestamp": (base + timedelta(seconds=2)).isoformat(),
            "role": "assistant",
            "content": "Starting security assessment of api.example.com",
        },
        {
            "timestamp": (base + timedelta(seconds=10)).isoformat(),
            "role": "assistant",
            "content": "Discovered open ports 80, 443, 8080",
        },
    ],
    "vulnerability_reports": [
        {
            "id": "vuln-001",
            "title": "SQL Injection in /api/users",
            "severity": "critical",
            "cvss": 9.8,
            "timestamp": (base + timedelta(seconds=60)).isoformat(),
            "target": "api.example.com",
            "endpoint": "/api/users",
            "method": "POST",
        },
        {
            "id": "vuln-002",
            "title": "Missing Rate Limiting on Auth Endpoint",
            "severity": "high",
            "cvss": 7.5,
            "timestamp": (base + timedelta(seconds=120)).isoformat(),
            "target": "api.example.com",
            "endpoint": "/api/auth/login",
            "method": "POST",
        },
        {
            "id": "vuln-003",
            "title": "Reflected XSS in Search Parameter",
            "severity": "medium",
            "cvss": 6.1,
            "timestamp": (base + timedelta(seconds=180)).isoformat(),
            "target": "api.example.com",
            "endpoint": "/api/search",
            "method": "GET",
        },
    ],
    "scan_results": {"scan_completed": True, "executive_summary": "Found 3 vulnerabilities."},
    "scan_config": {
        "targets": [{"original": "api.example.com"}],
        "run_name": "smoke-test-video-001",
    },
    "run_metadata": {"status": "complete"},
    "streaming_content": {},
    "streaming_thinking": {},
    "interrupted_content": {},
    "latest_browser_screenshots": {},
}

(run_dir / "checkpoint.json").write_text(json.dumps(checkpoint, indent=2))
print("Fake run created at", run_dir)
EOF
```

**Step 2: Run video export**

```bash
esprit report smoke-test-video-001 --video --speed 10
```

Expected output:
```
Video: esprit_runs/smoke-test-video-001/replay.mp4
```

**Step 3: Verify the MP4**

```bash
ffprobe -v quiet -show_entries stream=width,height,codec_name,duration \
  -of default=noprint_wrappers=1 esprit_runs/smoke-test-video-001/replay.mp4
```

Expected:
```
codec_name=h264
width=1920
height=1080
duration=<N>  # roughly (total_event_span_seconds / speed) + intro + outro
```

**Step 4: Open and eyeball it**

```bash
open esprit_runs/smoke-test-video-001/replay.mp4
```

Verify:
- [ ] Clean logo fade-in (no glitch)
- [ ] Window drops in smoothly with spring
- [ ] Agents appear in left panel with fade
- [ ] Terminal lines stream in the center
- [ ] Vuln cards slam in from the right with shockwave
- [ ] Summary outro card rises, fades to black
- [ ] Esprit logo at end

**Step 5: Commit**

```bash
git add esprit_runs/  # only if you want to commit the smoke test artifact — probably don't
git commit -m "feat: video export end-to-end smoke test passing"
```

---

### Task 8: Add `Tracer.load_from_dir` (if it doesn't exist)

**Files:**
- Modify: `esprit/telemetry/tracer.py`

**Step 1: Check whether it exists first**

```bash
grep -n "load_from_dir\|classmethod.*load\|checkpoint" /Users/shauryagupta/Downloads/Esprit/esprit/telemetry/tracer.py | head -20
```

**Step 2: If it doesn't exist, look at how checkpoints are saved**

```bash
grep -n "checkpoint\|json.dump\|write_text" /Users/shauryagupta/Downloads/Esprit/esprit/telemetry/tracer.py | head -20
```

**Step 3: Add the classmethod based on what you find**

A typical implementation:
```python
@classmethod
def load_from_dir(cls, run_dir: Path) -> "Tracer | None":
    """Load a Tracer from a saved checkpoint directory. Returns None if not found."""
    checkpoint_path = run_dir / "checkpoint.json"
    if not checkpoint_path.exists():
        return None
    try:
        data = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        tracer = cls(run_name=data.get("run_name"))
        tracer.run_id = data.get("run_id", tracer.run_id)
        tracer.start_time = data.get("start_time", tracer.start_time)
        tracer.end_time = data.get("end_time")
        tracer.agents = data.get("agents", {})
        tracer.tool_executions = {int(k): v for k, v in data.get("tool_executions", {}).items()}
        tracer.chat_messages = data.get("chat_messages", [])
        tracer.vulnerability_reports = data.get("vulnerability_reports", [])
        tracer.scan_results = data.get("scan_results")
        tracer.scan_config = data.get("scan_config")
        tracer.run_metadata = data.get("run_metadata", tracer.run_metadata)
        tracer._run_dir = run_dir
        return tracer
    except Exception as e:
        logger.warning("Failed to load tracer from %s: %s", run_dir, e)
        return None
```

**Step 4: Write a test**

```python
# tests/telemetry/test_tracer_load.py
import json
from pathlib import Path
import pytest
from esprit.telemetry.tracer import Tracer

def test_load_from_dir(tmp_path):
    data = {
        "run_id": "test-123",
        "run_name": "test-scan",
        "start_time": "2026-02-18T10:00:00+00:00",
        "end_time": "2026-02-18T10:04:31+00:00",
        "agents": {},
        "tool_executions": {},
        "chat_messages": [],
        "vulnerability_reports": [],
        "scan_config": {"targets": [{"original": "example.com"}]},
        "run_metadata": {"status": "complete"},
    }
    (tmp_path / "checkpoint.json").write_text(json.dumps(data))
    tracer = Tracer.load_from_dir(tmp_path)
    assert tracer is not None
    assert tracer.run_id == "test-123"
    assert tracer.run_name == "test-scan"

def test_load_from_dir_missing_returns_none(tmp_path):
    tracer = Tracer.load_from_dir(tmp_path)
    assert tracer is None
```

**Step 5: Run tests**

```bash
python -m pytest tests/telemetry/test_tracer_load.py -v --no-cov
```

**Step 6: Commit**

```bash
git add esprit/telemetry/tracer.py tests/telemetry/test_tracer_load.py
git commit -m "feat: add Tracer.load_from_dir for CLI video export"
```

---

## Notes

- Tasks 2, 3, 4, 5, 8 are independent of each other and can be parallelized if using subagent-driven mode
- Task 8 must come before Task 5 (CLI) can be fully tested end-to-end
- Task 3 (the HTML template) is the most creative/iterative piece — expect to tweak it after the first visual review
- The `innerHTML` usage in the JS is intentional and safe — all data comes from our own Jinja-rendered JSON, never from user input at runtime
- No glitch effects anywhere in the template. Fade-in only for logos. Spring physics for the window. Smooth easing everywhere.
