# Esprit Platform Changes — February 2026

## Overview

The AWS-hosted web version (`junaid-mahmood/Esprit`, serving esprit.dev) has been synced to full parity with the opensource CLI (`improdead/Esprit`). This document covers everything that changed, how the hosted deployment works, and how to revert.

---

## What Changed

### 1. White-Box Vulnerability Remediation

**New feature.** When scanning local source code (white-box mode), the agent now automatically patches vulnerabilities.

**How it works:**
- System prompt instructs agents to follow a 4-step workflow per vulnerability: **Discovery → Validation → Reporting → Fixing**
- Fixing Agents use `str_replace_editor` to patch code and verify the exploit no longer works
- `finish_scan` has a soft-gate: it bounces the root agent if fixing agents haven't covered all reported vulns
- After 2 bounces per scan, the gate allows through (escape hatch to prevent deadlocks)
- `is_whitebox` flag is threaded from root agent through all child agents via `AgentState`
- New skill file: `esprit/skills/vulnerabilities/remediation.md` teaches fixing agents fix patterns for SQLi, XSS, IDOR, SSRF, RCE, path traversal, CSRF, JWT

**Files:** `system_prompt.jinja`, `state.py`, `base_agent.py`, `agents_graph_actions.py`, `finish_actions.py`, `root_agent.md`, `remediation.md`

### 2. Remediation Diff Persistence

**New feature.** File edits made inside ephemeral sandbox containers are now captured and persisted before the container is destroyed.

**How it works:**
- `file_edit_actions.py` tracks every mutating edit in an in-memory `_edit_log`
- `tool_server.py` exposes a `GET /diffs` endpoint returning all edits
- Before sandbox teardown, `extract_and_save_diffs()` calls `/diffs` and writes:
  - `esprit_runs/<scan>/patches/edits.json` (machine-readable)
  - `esprit_runs/<scan>/patches/remediation.patch` (unified diff)
- **Hosted mode (AWS):** When `S3_BUCKET` and `SCAN_ID` env vars are set, the patch is also uploaded to `s3://{bucket}/patches/{scan_id}.patch` — the backend's existing `GET /scans/{scan_id}/patch` endpoint serves it as a presigned download URL

**Files:** `file_edit_actions.py`, `tool_server.py`, `runtime.py`, `docker_runtime.py`, `cloud_runtime.py`, `runtime/__init__.py`, `cli.py`, `tui.py`

### 3. Agent Spawning Guard

`MAX_AGENTS = 10` cap on concurrent active agents (running/waiting/stopping). Prevents runaway spawning while allowing long scans — finished agents don't count toward the limit.

**File:** `agents_graph_actions.py`

### 4. TUI Theme System

- Replaced dead `theme.py` + 33 JSON theme files with `theme_tokens.py`
- 6 built-in themes: esprit, ember, matrix, glacier, crt, **sakura** (new)
- Sakura added to match launchpad's 6-theme selector
- All renderers use `theme_tokens` consistently
- Emojis replaced with `[marker]` text labels for terminal compatibility

**Files:** `theme_tokens.py` (new), `theme.py` + `themes/*.json` (deleted), all `*_renderer.py` files, `tui.py`, `utils.py`

### 5. Image Rendering

5-tier rendering cascade: Kitty TGP → Sixel → halfcell → quarter-block → half-block. `textual-image` promoted from optional to required dependency.

**Files:** `image_protocol.py`, `image_renderer.py`, `image_widget.py` (all new)

### 6. LLM Resilience

- Auto-resume sub-agents on transient LLM failures (HTTP 408/429/5xx)
- Stream idle timeout aborts stalled LLM responses
- OpenCode fallback chain tries alternative free models on rate limits
- Centralized API base resolution in `api_base.py`

**Files:** `base_agent.py`, `llm.py`, `api_base.py` (new)

### 7. Provider Updates

- OpenCode Zen provider (`opencode_zen.py`, new)
- Live model discovery via `/models` endpoint with 5-min cache
- Public no-auth models (`sk-opencode-public-noauth`)
- Cost estimation and pricing system (`cost_estimator.py`, `pricing.py`)

### 8. Interface / Launchpad

- Interactive project setup wizard (`launchpad.py`, 1328 lines)
- Self-update mechanism (`updater.py`)
- `spd` CLI alias
- New TUI screens: VulnerabilityOverlay (Ctrl+V), AgentHealth (Ctrl+H), Update (Ctrl+U), BrowserPreview

### 9. Build / Scripts

- `start.sh`: Poetry dep sync on launch, venv/node fallbacks, update checker for binary installs
- `install.sh`: `--force` flag, temp cleanup trap, arm64 Docker fallback, always-pull strategy
- `docker-entrypoint.sh`: Startup log cleanup
- `.gitignore`: Added `.mypy_cache/`, `.ruff_cache/`, `.terraform/`, `node_modules/`
- Dockerfile: Removed unused `notes/` tool COPY

---

## AWS Deployment Details

### What's deployed

| Component | Version | Details |
|---|---|---|
| Sandbox Docker image | `esprit-sandbox:v25` | ECR `083880123072.dkr.ecr.us-east-1.amazonaws.com` |
| ECS task definition | `esprit-prod-sandbox:35` | Image v25, `S3_BUCKET` + `ESPRIT_SANDBOX_MODE` env vars |
| Orchestrator image | `esprit-orchestrator:v12` | Unchanged (backend doesn't import from `esprit/`) |
| Git branch | `feat/migrate-to-opensource` | On `junaid-mahmood/Esprit` |

### Hosted remediation flow

```
User submits white-box scan on esprit.dev
    → Backend launch_scan_task() → ECS Fargate container
    → Container: tool server starts, agent loop runs
    → Fixing agents patch code via str_replace_editor
    → Edits tracked in _edit_log (in-memory)
    → finish_scan: remediation gate checks coverage
    → cleanup: extract_and_save_diffs()
        → GET /diffs from tool server
        → Upload to s3://esprit-prod-scan-results/patches/{scan_id}.patch
    → Backend: GET /scans/{scan_id}/patch → presigned S3 download URL
    → User downloads patch from web UI
```

### Environment variables (sandbox container)

| Var | Value | Purpose |
|---|---|---|
| `ESPRIT_SANDBOX_MODE` | `true` | Enables sandbox tool routing |
| `S3_BUCKET` | `esprit-prod-scan-results-083880123072` | Patch upload destination |
| `CAIDO_PORT` | `8080` | Proxy port |
| `SCAN_ID` | (per-scan) | Passed by backend at launch |

### Loop prevention

| Guard | Limit | Behavior at limit |
|---|---|---|
| Remediation bounce | 2 per scan (keyed by agent_id) | Allows `finish_scan` through |
| MAX_AGENTS | 10 active (running/waiting/stopping) | Returns error, caller retries or continues |
| Agent iterations | 300 per sub-agent | Agent loop exits |
| Diff extraction | 15s timeout | Returns empty list |

---

## How to Revert

### Full revert (all code changes)

```bash
cd /Users/dekai/documents/esprit/Esprit
git checkout feat/native-tool-calling
# Or use the backup tag:
git checkout backup/pre-migration-20260223
```

### Revert Docker image

The old image `esprit-sandbox:v24` is still in ECR. Register a new task def revision pointing to it:

```bash
# Get current task def, change image back to v24, register
aws ecs describe-task-definition --task-definition esprit-prod-sandbox \
  --query 'taskDefinition' --output json | \
  python3 -c "
import sys,json
td=json.loads(sys.stdin.read())
td['containerDefinitions'][0]['image']='083880123072.dkr.ecr.us-east-1.amazonaws.com/esprit-sandbox:v24'
for k in ['taskDefinitionArn','revision','status','requiresAttributes','compatibilities','registeredAt','registeredBy']:
    td.pop(k,None)
print(json.dumps(td))
" | aws ecs register-task-definition --cli-input-json file:///dev/stdin
```

### Revert ECS task def (remove S3_BUCKET)

Task definition revision 33 was the last pre-migration version. The backend's `launch_scan_task()` references `esprit-prod-sandbox` which resolves to the latest active revision.

### Revert specific features only

```bash
# Revert remediation only
git checkout backup/pre-migration-20260223 -- \
  esprit/tools/finish/finish_actions.py \
  esprit/agents/EspritAgent/system_prompt.jinja \
  esprit/agents/state.py \
  esprit/skills/coordination/root_agent.md
rm esprit/skills/vulnerabilities/remediation.md

# Revert diff persistence only
git checkout backup/pre-migration-20260223 -- \
  esprit/runtime/ esprit/tools/file_edit/ \
  esprit/interface/cli.py esprit/interface/tui.py

# Revert TUI themes only
git checkout backup/pre-migration-20260223 -- \
  esprit/interface/tui.py esprit/interface/utils.py \
  esprit/interface/assets/ esprit/interface/tool_components/
rm esprit/interface/theme_tokens.py
```

---

## Test Results

398 tests pass on both repos. Key test files added:

- `tests/tools/test_finish_remediation.py` — 14 tests: coverage check, per-scan bounce counter, skills format validation
- `tests/interface/test_tui_theme_tokens.py` — Updated for 6 themes including sakura
