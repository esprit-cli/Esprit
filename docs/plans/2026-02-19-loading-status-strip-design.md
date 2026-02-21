# Claude-Style Activity Strip (TUI + Web) — Design Document

**Date:** 2026-02-19
**Status:** Approved

## Summary

This design adds an always-visible, Claude Code–style activity strip to both the terminal TUI and the web dashboard. The strip shows a hex-style loading indicator, live "what the agent is doing" text, and compact token/time metrics. When work is done, the strip remains visible but dims so operators always retain context without noisy animation.

## Problem Statement

Current status UX has two gaps:

1. The TUI status row can disappear in states where operators still expect context.
2. The web footer status does not provide a unified activity line with low-jitter metrics.

This creates uncertainty during long scans and inconsistent operator feedback across interfaces.

## Goals

- Always show an activity strip in TUI and web dashboard.
- Use a hex-style spinner (not basic circle-dot spinner).
- Show hacker-vibe activity wording that updates with runtime state.
- Keep token and elapsed time metrics visible with minimal layout shift.
- Keep strip visible in done states, but dim and non-animated.

## Non-Goals

- No change to scan orchestration, watchdog policy, or agent runtime behavior.
- No redesign of chat history, tool timeline, or vulnerability panel.
- No backend API contract changes outside existing tracer-bridge payloads.

## UX Behavior

### TUI strip

Location: between live stream and chat input (existing `#agent_status_display`).

States:
- **Idle (no selected/active agent):** visible, dim, static text (`Idle — waiting for agent activity`)
- **Active (running/waiting/recovered):** animated hex frame + dynamic activity text + right-side metrics
- **Done (`completed` / `finished` / `stopped` / `llm_failed`):** visible, dimmed, no spinner animation

Activity text priority:
1. Compaction / watchdog diagnostics
2. Thinking / streaming state
3. Current activity summary/tool context
4. Initializing fallback

### Web strip

Location: footer `#status-bar`, with a dedicated activity segment.

States mirror TUI semantics:
- active with animated hex spinner
- done with dimmed strip and frozen spinner frame
- idle with fallback text

Metrics:
- total input/output tokens
- tokens/sec
- elapsed time

## Anti-Shift Strategy (Web)

- Reserve strip height and metric widths via fixed slot/chip sizing.
- Use `font-variant-numeric: tabular-nums` for stable number rendering.
- Use single-line ellipsis for activity text (`overflow: hidden; text-overflow: ellipsis; white-space: nowrap`).
- Keep spinner width fixed even when state changes.

## Data Sources

No new APIs required.

- TUI uses existing tracer + status derivation in `EspritTUIApp`.
- Web uses existing `stats`, `agents`, `streaming`, and selected-agent context in `app.js`.
- Token/time values come from existing `TracerBridge._get_stats()` payload.

## Testing Strategy

### TUI tests
- Extend `tests/interface/test_tui_layout.py` to assert:
  - always-visible strip fallback content exists
  - hex spinner rendering path exists
  - done states use dimmed semantics rather than hidden semantics

### Web tests
- Extend `tests/gui/test_server.py` to assert:
  - new activity-strip DOM IDs in `index.html`
  - activity-strip renderer and hex frames in `app.js`
  - anti-shift CSS rules in `style.css`

## Acceptance Criteria

- TUI strip remains visible in idle/active/done states.
- Spinner style is hex-themed during active execution.
- Done strip is visible but visually subdued.
- Web strip shows spinner + activity + token/time with reduced jitter.
- Focused test suite for touched areas passes.
