# Loading Status Strip (TUI + Web) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an always-visible Claude-style activity strip with hex spinner + activity text + token/time metrics in both TUI and web dashboard, with dimmed done-state behavior and visibility-focused test coverage.

**Architecture:** Reuse existing status update loops in `EspritTUIApp` and dashboard `app.js`, but normalize rendering so the strip is never hidden and instead switches between active/dim modes. Build a shared, fixed-width metrics presentation to reduce jitter (especially in web CSS). Keep changes scoped to status-strip rendering + styles + targeted tests only.

**Tech Stack:** Python 3.12, Textual TUI, Rich `Text`, vanilla JS dashboard (`app.js`), CSS, pytest

---

### Task 1: Add TUI tests for always-visible strip + hex spinner + dimmed done states

**Files:**
- Test: `tests/interface/test_tui_layout.py`
- Modify (implementation target for next task): `esprit/interface/tui.py`

**Step 1: Write failing tests for TUI strip behavior**

```python
# tests/interface/test_tui_layout.py

def test_status_display_uses_hex_spinner_frames() -> None:
    import inspect
    from esprit.interface.tui import EspritTUIApp

    source = inspect.getsource(EspritTUIApp._get_status_display_content)
    assert 'HEX_SPINNER_FRAMES' in source


def test_status_display_keeps_done_states_visible_and_dimmed() -> None:
    import inspect
    from esprit.interface.tui import EspritTUIApp

    source = inspect.getsource(EspritTUIApp._get_status_display_content)
    assert 'Agent completed' in source
    assert 'style="dim"' in source or "style='dim'" in source


def test_update_agent_status_display_uses_idle_fallback_instead_of_hiding() -> None:
    import inspect
    from esprit.interface.tui import EspritTUIApp

    source = inspect.getsource(EspritTUIApp._update_agent_status_display)
    assert 'Idle — waiting for agent activity' in source
    assert 'status_display.add_class, "hidden"' not in source
```

**Step 2: Run tests and confirm they fail**

Run: `poetry run pytest tests/interface/test_tui_layout.py::TestTracerCompatibility -q`

Expected: FAIL because current implementation still hides strip and does not use hex spinner constant.

**Step 3: Do not implement yet (TDD discipline)**

No production code changes in this task.

**Step 4: Commit failing tests**

```bash
git add tests/interface/test_tui_layout.py
git commit -m "test: add failing coverage for always-visible TUI activity strip"
```

---

### Task 2: Implement TUI always-visible activity strip with hex spinner

**Files:**
- Modify: `esprit/interface/tui.py`
- Modify: `esprit/interface/assets/tui_styles.tcss`
- Test: `tests/interface/test_tui_layout.py`

**Step 1: Implement hex spinner frames and strip behavior in `_get_status_display_content`**

```python
# esprit/interface/tui.py
HEX_SPINNER_FRAMES = ["⬢", "⬡", "⬢", "⬡"]

# In running/watchdog branches:
frame = HEX_SPINNER_FRAMES[self._stats_spinner_frame % len(HEX_SPINNER_FRAMES)]
animated_text.append(f"{frame} ", style="#22d3ee")
animated_text.append("Tracing attack graph", style="#22d3ee")

# In done statuses:
text = Text()
text.append("⬡ ", style="dim")
text.append("Agent completed", style="dim")
```

**Step 2: Stop hiding status strip in `_update_agent_status_display`; render idle fallback**

```python
# esprit/interface/tui.py
if not self.selected_agent_id:
    idle = Text()
    idle.append("⬡ ", style="dim")
    idle.append("Idle — waiting for agent activity", style="dim")
    self._safe_widget_operation(status_text.update, idle)
    self._safe_widget_operation(keymap_indicator.update, Text(""))
    self._safe_widget_operation(status_display.remove_class, "hidden")
    return
```

**Step 3: Keep strip subtle by default in TCSS, brighter in active updates only**

```css
/* esprit/interface/assets/tui_styles.tcss */
#agent_status_display {
  height: 1;
  background: #0a1520 55%;
}
#status_text { color: #7f6a6a; }
#keymap_indicator { color: #7f6a6a; }
```

**Step 4: Run targeted tests and confirm pass**

Run: `poetry run pytest tests/interface/test_tui_layout.py tests/interface/test_stats_panel.py -q`

Expected: PASS.

**Step 5: Commit TUI implementation**

```bash
git add esprit/interface/tui.py esprit/interface/assets/tui_styles.tcss tests/interface/test_tui_layout.py
git commit -m "feat: add always-visible hex activity strip in TUI"
```

---

### Task 3: Add web dashboard tests for activity strip structure and anti-shift styling

**Files:**
- Test: `tests/gui/test_server.py`
- Modify (implementation target for next task):
  - `esprit/gui/static/index.html`
  - `esprit/gui/static/app.js`
  - `esprit/gui/static/style.css`

**Step 1: Write failing web tests first**

```python
# tests/gui/test_server.py

def test_index_html_contains_activity_strip_slots(self) -> None:
    from esprit.gui.server import _STATIC_DIR
    content = (_STATIC_DIR / "index.html").read_text()
    assert "status-activity" in content
    assert "status-activity-text" in content
    assert "status-activity-metrics" in content


def test_app_js_contains_activity_strip_renderer(self) -> None:
    from esprit.gui.server import _STATIC_DIR
    content = (_STATIC_DIR / "app.js").read_text()
    assert "_renderActivityStrip" in content
    assert "HEX_SPINNER_FRAMES" in content


def test_style_css_contains_anti_shift_activity_rules(self) -> None:
    from esprit.gui.server import _STATIC_DIR
    content = (_STATIC_DIR / "style.css").read_text()
    assert "#status-activity" in content
    assert "font-variant-numeric: tabular-nums" in content
    assert "text-overflow: ellipsis" in content
```

**Step 2: Run tests and confirm failure**

Run: `poetry run pytest tests/gui/test_server.py::TestGUIServerStaticDir -q`

Expected: FAIL because activity strip IDs/renderer/styles are not present yet.

**Step 3: Do not implement yet (TDD discipline)**

No static file edits in this task.

**Step 4: Commit failing tests**

```bash
git add tests/gui/test_server.py
git commit -m "test: add failing web activity strip and anti-shift checks"
```

---

### Task 4: Implement web activity strip with hex spinner, tokens/time, and low-jitter layout

**Files:**
- Modify: `esprit/gui/static/index.html`
- Modify: `esprit/gui/static/app.js`
- Modify: `esprit/gui/static/style.css`
- Test: `tests/gui/test_server.py`

**Step 1: Add dedicated activity strip nodes in footer HTML**

```html
<!-- esprit/gui/static/index.html -->
<footer id="status-bar">
  <span class="status-bar-item" id="status-run-id">...</span>
  <span class="status-bar-item" id="status-duration">...</span>
  <span class="status-bar-item status-activity" id="status-activity">
    <span id="status-activity-spinner">⬡</span>
    <span id="status-activity-text">Idle — waiting for agent activity</span>
    <span id="status-activity-metrics"></span>
  </span>
  <span class="status-bar-item" id="status-results-path">...</span>
</footer>
```

**Step 2: Implement activity-strip rendering in `app.js`**

```javascript
// esprit/gui/static/app.js
const HEX_SPINNER_FRAMES = ['⬢', '⬡', '⬢', '⬡'];

_renderActivityStrip() {
  const strip = document.getElementById('status-activity');
  const spinner = document.getElementById('status-activity-spinner');
  const text = document.getElementById('status-activity-text');
  const metrics = document.getElementById('status-activity-metrics');
  if (!strip || !spinner || !text || !metrics) return;

  const selected = this.agents.find(a => a.id === this.selectedAgentId) || this.agents[0] || null;
  const status = (selected && selected.status) || (this.stats && this.stats.status) || 'running';
  const done = ['completed', 'finished', 'stopped', 'llm_failed'].includes(status);

  const frame = HEX_SPINNER_FRAMES[Math.floor(Date.now() / 250) % HEX_SPINNER_FRAMES.length];
  spinner.textContent = done ? '⬡' : frame;

  // derive text from streaming/activity
  const stream = selected ? (this.streaming[selected.id] || '') : '';
  const baseText = stream.trim() ? stream.trim().split('\n').slice(-1)[0] : 'Idle — waiting for agent activity';
  text.textContent = baseText;

  const total = (this.stats && this.stats.llm && this.stats.llm.total) || {};
  const inTok = this._fmtNum(total.input_tokens || 0);
  const outTok = this._fmtNum(total.output_tokens || 0);
  const tps = this.stats && this.stats.tokens_per_second ? this.stats.tokens_per_second : 0;
  const elapsed = document.querySelector('#status-duration .status-text')?.textContent?.trim() || '0:00';
  metrics.textContent = `${inTok}↓ ${outTok}↑  ${tps} tok/s  ${elapsed}`;

  strip.classList.toggle('is-dim', done);
}
```

Call `_renderActivityStrip()` from `_renderStats()`, `_renderStatusBar()`, and `_renderAll()`.

**Step 3: Add anti-shift CSS constraints**

```css
/* esprit/gui/static/style.css */
#status-activity {
  flex: 1;
  min-width: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
#status-activity-spinner {
  width: 1.4ch;
  text-align: center;
  color: var(--accent);
}
#status-activity-text {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
#status-activity-metrics {
  width: 32ch;
  text-align: right;
  color: var(--text-dim);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
#status-activity.is-dim {
  opacity: 0.62;
}
```

**Step 4: Run targeted GUI tests and confirm pass**

Run: `poetry run pytest tests/gui/test_server.py -q`

Expected: PASS.

**Step 5: Commit web implementation**

```bash
git add esprit/gui/static/index.html esprit/gui/static/app.js esprit/gui/static/style.css tests/gui/test_server.py
git commit -m "feat: add web activity strip with hex spinner and low-shift metrics"
```

---

### Task 5: Run focused verification suite for TUI + web strip

**Files:**
- Test: `tests/interface/test_tui_layout.py`
- Test: `tests/interface/test_stats_panel.py`
- Test: `tests/gui/test_server.py`

**Step 1: Run focused tests**

Run:

```bash
poetry run pytest tests/interface/test_tui_layout.py tests/interface/test_stats_panel.py tests/gui/test_server.py -q
```

Expected: PASS (0 failures).

**Step 2: Run targeted lint for touched files**

Run:

```bash
poetry run ruff check esprit/interface/tui.py esprit/interface/assets/tui_styles.tcss esprit/gui/static/app.js esprit/gui/static/style.css tests/interface/test_tui_layout.py tests/gui/test_server.py
```

Expected: PASS, or clearly documented pre-existing lint in unaffected regions.

**Step 3: Manual UX smoke checks**

Run a scan and validate:
- TUI strip always visible between live stream and input
- active hex spinner shown while running
- done state still visible but dimmed
- web footer activity strip shows spinner + text + metrics
- reduced jitter when tokens/time update

**Step 4: Commit verification-only adjustments (if any)**

```bash
git add tests/interface/test_tui_layout.py tests/gui/test_server.py esprit/gui/static/style.css
git commit -m "test: verify always-visible loading strip behavior in TUI and web"
```

---

## Execution Notes

- Use strict @superpowers:test-driven-development per task (red -> green -> minimal code).
- Use @superpowers:verification-before-completion before any success claim.
- Keep changes scoped to strip rendering/styling only; do not refactor unrelated dashboard/TUI subsystems.
- Preserve existing safe DOM approach (`createElement`, `textContent`, no `innerHTML`).
