# Anti-Freeze Watchdog & TUI Reliability — Design Document

**Date:** 2026-02-19
**Status:** Approved

## Summary

This design eliminates scan "freeze" behavior by adding a bounded watchdog in the agent loop and surfacing stall/recovery diagnostics in the TUI. The goal is not to make every external dependency infinitely reliable; the goal is to guarantee progress, bounded waiting, and clear operator feedback when the system is stalled, recovering, or done.

## Problem Statement

Users reported overnight scans appearing frozen. Root causes were:

1. Long-running LLM/tool calls without bounded timeout handling.
2. Limited heartbeat diagnostics for stale-execution detection.
3. UI status mismatches (`finished` vs `completed`) that made done agents look active or unclear.
4. Weak visibility into stall/recovery transitions in the status line and subagent dashboard.

## Goals

- Detect stale agent execution via heartbeat age.
- Recover automatically a limited number of times.
- Convert hard stalls into explicit failed/waiting states (not silent hangs).
- Keep token stats visible even during initializing/watchdog states.
- Normalize TUI done semantics (`completed` + `finished`) to avoid false "still running" signals.

## Non-Goals

- Guarantee success of every scan regardless of upstream model/API outages.
- Add new scan features unrelated to freeze prevention.
- Add global architectural refactors beyond watchdog + TUI reliability.

## Architecture

### 1) BaseAgent watchdog core

`BaseAgent` adds runtime watchdog config and wraps both LLM iteration and tool execution in timeout boundaries. Heartbeats are emitted at key phases. If heartbeat becomes stale:

- If recovery budget remains: cancel current execution, record recovery, resume running.
- If budget exhausted: mark `llm_failed` + waiting-for-input with clear error text.

Policy is intentionally strict and bounded:

- `stall_policy`: `auto_recover`
- `llm_watchdog_timeout_s`: bounded positive int
- `tool_watchdog_timeout_s`: bounded positive int
- `stall_grace_period_s`: bounded positive int
- `max_stall_recoveries`: bounded non-negative int

### 2) Agent state metadata

`AgentState` stores heartbeat and recovery metadata:

- `last_heartbeat_at`, `heartbeat_phase`, `heartbeat_detail`
- `stall_count`, `last_recovery_at`, `last_recovery_reason`

This keeps recovery behavior observable and testable.

### 3) Tracer heartbeat API

`Tracer` receives and exposes heartbeat snapshots per agent:

- `touch_agent_heartbeat(agent_id, phase, detail)`
- `get_agent_heartbeat(agent_id)`

This is the bridge from runtime state to TUI diagnostics.

### 4) TUI reliability and watchdog UX

TUI consumes tracer heartbeat and renders watchdog state:

- Status line watchdog indicators for stale/recovered conditions.
- Subagent dashboard stale/recovered labels and diagnostics.
- Done-state normalization includes `finished` wherever completion semantics are used.
- Agent tree icon mapping includes `finished` and `stalled_recovered`.
- Child-running checks treat `stalled_recovered` as active.

Token stats remain visible in running/initializing/watchdog branches.

## Recovery Policy

1. Ignore stale checks while waiting for user input or after `llm_failed` is already set.
2. Ignore stale checks if there is no active current task.
3. On stale heartbeat with remaining budget:
   - cancel current task
   - increment `stall_count`
   - record reason/timestamp
   - transiently mark `stalled_recovered`, then continue `running`
4. On stale heartbeat with exhausted budget:
   - enter waiting state with `llm_failed=True`
   - set explicit error message

## Testing Strategy

### Unit tests

- `tests/agents/test_base_agent.py`
  - timeout behavior for LLM/tool execution
  - config clamping/validation
  - stale recovery path
  - max-recovery exhaustion path
  - guard conditions (waiting state / no active task)

- `tests/telemetry/test_tracer.py`
  - heartbeat store/retrieve semantics

- `tests/interface/test_tui_layout.py`
  - `finished` done semantics
  - watchdog UI source/behavior checks
  - status icon mapping for `finished`/`stalled_recovered`
  - stale/recovered watchdog state derivation
  - recovered treated as active in child-running checks

### Verification commands

- `poetry run pytest tests/interface/test_tui_layout.py tests/interface/test_stats_panel.py tests/agents/test_base_agent.py tests/telemetry/test_tracer.py -q`

## Acceptance Criteria

- Scan cannot silently hang on one stalled call forever.
- Stalled execution is either auto-recovered (bounded) or converted to explicit failure/waiting.
- TUI shows consistent done state for finished agents.
- TUI displays watchdog diagnostics when heartbeat is stale/recovered.
- Focused anti-freeze test suite passes.
