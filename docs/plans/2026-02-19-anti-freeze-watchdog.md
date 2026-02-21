# Anti-Freeze Watchdog Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure scans never appear frozen by enforcing bounded watchdog recovery and surfacing clear stall/recovery state in the TUI.

**Architecture:** Add heartbeat/recovery metadata at the agent and tracer layers, enforce LLM/tool timeout boundaries plus bounded auto-recovery in `BaseAgent`, and render watchdog diagnostics in TUI status/dashboard views. Keep done-state semantics consistent (`completed` + `finished`) and preserve token visibility in all running/watchdog branches.

**Tech Stack:** Python 3.12, asyncio (`wait_for` timeouts), Textual TUI, Rich `Text`, pytest, unittest.mock

---

### Task 1: Add heartbeat metadata plumbing (AgentState + Tracer)

**Files:**
- Modify: `esprit/agents/state.py`
- Modify: `esprit/telemetry/tracer.py`
- Test: `tests/telemetry/test_tracer.py`

**Step 1: Write failing tests for heartbeat storage and retrieval**

```python
# tests/telemetry/test_tracer.py

def test_touch_agent_heartbeat_stores_values_under_agent() -> None:
    tracer = Tracer("test-run")
    tracer.log_agent_creation("agent_1", "Agent 1", "Test task")

    tracer.touch_agent_heartbeat("agent_1", phase="before_llm_processing", detail="iter-3")

    heartbeat = tracer.agents["agent_1"].get("heartbeat")
    assert heartbeat["phase"] == "before_llm_processing"
    assert heartbeat["detail"] == "iter-3"


def test_get_agent_heartbeat_returns_dict_or_none() -> None:
    tracer = Tracer("test-run")
    tracer.log_agent_creation("agent_1", "Agent 1", "Test task")
    assert tracer.get_agent_heartbeat("agent_1") is None
```

**Step 2: Run tests and confirm failure**

Run: `poetry run pytest tests/telemetry/test_tracer.py::TestTracerHeartbeat -q`

Expected: FAIL due to missing heartbeat helper methods.

**Step 3: Implement state + tracer heartbeat API**

```python
# esprit/agents/state.py
last_heartbeat_at: str | None = None
heartbeat_phase: str | None = None
heartbeat_detail: str | None = None
stall_count: int = 0
last_recovery_at: str | None = None
last_recovery_reason: str | None = None


def touch_heartbeat(self, phase: str, detail: str | None = None) -> None:
    now = datetime.now(UTC).isoformat()
    self.last_heartbeat_at = now
    self.heartbeat_phase = phase
    self.heartbeat_detail = detail
```

```python
# esprit/telemetry/tracer.py

def touch_agent_heartbeat(self, agent_id: str, phase: str, detail: str | None = None) -> None:
    ...

def get_agent_heartbeat(self, agent_id: str) -> dict[str, Any] | None:
    ...
```

**Step 4: Re-run tests and confirm pass**

Run: `poetry run pytest tests/telemetry/test_tracer.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add esprit/agents/state.py esprit/telemetry/tracer.py tests/telemetry/test_tracer.py
git commit -m "feat: add agent heartbeat metadata and tracer heartbeat APIs"
```

---

### Task 2: Add bounded watchdog and timeout recovery in BaseAgent

**Files:**
- Modify: `esprit/agents/base_agent.py`
- Test: `tests/agents/test_base_agent.py`

**Step 1: Write failing watchdog tests first**

```python
# tests/agents/test_base_agent.py

def test_watchdog_values_are_clamped_and_policy_is_validated() -> None:
    agent = _make_agent(
        stall_policy="invalid",
        llm_watchdog_timeout_s=0,
        tool_watchdog_timeout_s=-5,
        stall_grace_period_s=-1,
        max_stall_recoveries=-2,
    )
    assert agent.stall_policy == "auto_recover"
    assert agent.llm_watchdog_timeout_s == 1


def test_execute_actions_timeout_raises_runtime_error() -> None:
    ...


def test_llm_timeout_routes_to_llm_error_handling() -> None:
    ...
```

**Step 2: Run tests and confirm failure**

Run: `poetry run pytest tests/agents/test_base_agent.py::TestBaseAgentWatchdogAndRecovery -q`

Expected: FAIL due to missing timeout/recovery logic.

**Step 3: Implement watchdog logic in `BaseAgent`**

```python
# esprit/agents/base_agent.py
self.llm_watchdog_timeout_s = max(1, _safe_int(config.get("llm_watchdog_timeout_s"), 360))
self.tool_watchdog_timeout_s = max(1, _safe_int(config.get("tool_watchdog_timeout_s"), 180))
self.stall_grace_period_s = max(1, _safe_int(config.get("stall_grace_period_s"), 90))
self.max_stall_recoveries = max(0, _safe_int(config.get("max_stall_recoveries"), 3))


def _touch_heartbeat(...):
    ...

def _is_heartbeat_stale(...):
    ...

def _maybe_auto_recover_stall(...):
    ...

# Wrap LLM and tool execution
should_finish = await asyncio.wait_for(iteration_task, timeout=self.llm_watchdog_timeout_s)
should_agent_finish = await asyncio.wait_for(tool_task, timeout=self.tool_watchdog_timeout_s)
```

**Step 4: Re-run tests and confirm pass**

Run: `poetry run pytest tests/agents/test_base_agent.py tests/telemetry/test_tracer.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add esprit/agents/base_agent.py tests/agents/test_base_agent.py tests/telemetry/test_tracer.py
git commit -m "feat: add bounded watchdog timeout and auto-recovery in base agent"
```

---

### Task 3: Expose watchdog and done-state consistency in TUI

**Files:**
- Modify: `esprit/interface/tui.py`
- Test: `tests/interface/test_tui_layout.py`

**Step 1: Write failing TUI tests**

```python
# tests/interface/test_tui_layout.py

def test_subagent_dashboard_includes_watchdog_diagnostics() -> None:
    source = inspect.getsource(EspritTUIApp._build_subagent_dashboard)
    assert "No heartbeat for" in source


def test_status_display_includes_watchdog_signals() -> None:
    source = inspect.getsource(EspritTUIApp._get_status_display_content)
    assert "Watchdog: no heartbeat" in source


def test_update_agent_node_maps_finished_and_recovered_statuses() -> None:
    ...
```

**Step 2: Run tests and confirm failure**

Run: `poetry run pytest tests/interface/test_tui_layout.py -q`

Expected: FAIL due to missing watchdog-state rendering or missing status mappings.

**Step 3: Implement watchdog rendering and status normalization**

```python
# esprit/interface/tui.py

def _get_watchdog_state(self, agent_id: str, status: str) -> dict[str, Any] | None:
    ...

status_styles = {
    "stalled_recovered": ("↻", "#f59e0b"),
    "finished": ("✓", "#22c55e"),
}

# dashboard diagnostics
card.append(f"No heartbeat for {age_s}s ({phase})", style="dim #f59e0b")

# status line diagnostics
animated_text.append("Watchdog: no heartbeat", style="#f59e0b")
animated_text.append("Watchdog recovered stall", style="#f59e0b")

# agent tree consistency
status_indicators = {
    "finished": "✓",
    "stalled_recovered": "↻",
}
```

**Step 4: Re-run tests and confirm pass**

Run: `poetry run pytest tests/interface/test_tui_layout.py tests/interface/test_stats_panel.py -q`

Expected: PASS.

**Step 5: Commit**

```bash
git add esprit/interface/tui.py tests/interface/test_tui_layout.py tests/interface/test_stats_panel.py
git commit -m "fix: expose watchdog stall/recovery diagnostics in TUI"
```

---

### Task 4: Run anti-freeze verification suite

**Files:**
- Test: `tests/agents/test_base_agent.py`
- Test: `tests/telemetry/test_tracer.py`
- Test: `tests/interface/test_tui_layout.py`
- Test: `tests/interface/test_stats_panel.py`

**Step 1: Run focused anti-freeze regression suite**

Run:

```bash
poetry run pytest tests/interface/test_tui_layout.py tests/interface/test_stats_panel.py tests/agents/test_base_agent.py tests/telemetry/test_tracer.py -q
```

Expected: PASS (0 failures).

**Step 2: Run targeted lint check for touched files**

Run:

```bash
poetry run ruff check esprit/interface/tui.py tests/interface/test_tui_layout.py tests/agents/test_base_agent.py tests/telemetry/test_tracer.py
```

Expected: If failures are pre-existing in `tui.py`, document them and avoid claiming full-project lint cleanliness.

**Step 3: Optional smoke run for operator UX**

Run: `poetry run esprit scan <target>`

Manual checks:
- stale/recovered signal appears in status line/dashboard when heartbeat is stale
- token stats remain visible during running/watchdog states
- done agents display correctly for both `completed` and `finished`

**Step 4: Commit verification-only adjustments (if any)**

```bash
git add tests/interface/test_tui_layout.py esprit/interface/tui.py
git commit -m "test: expand anti-freeze watchdog regression coverage"
```

---

## Execution Notes

- Follow strict @superpowers:test-driven-development for each task (fail first, then minimal fix).
- Use @superpowers:verification-before-completion before marking each task done.
- Avoid unrelated refactors in `esprit/interface/tui.py`; make only anti-freeze/watchdog changes.
- Keep compatibility with existing done-state semantics (`completed`, `finished`, `stopped`, `llm_failed`).
