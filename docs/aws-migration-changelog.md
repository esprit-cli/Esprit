# AWS-Hosted to Opensource: Changelog & Migration Plan

> **Generated**: 2026-02-23
> **Source of truth**: `improdead/Esprit` (opensource)
> **AWS-hosted repo**: `junaid-mahmood/Esprit` (branch: `feat/native-tool-calling`)
> **Cloud repo**: `improdead/Esprit` (Esprit-cloud, branch: `main`)

---

## Table of Contents

1. [Repository Overview](#1-repository-overview)
2. [Complete Changelog](#2-complete-changelog)
   - [Core Renames (Strix → Esprit)](#21-core-renames-strix--esprit)
   - [Agent System](#22-agent-system)
   - [LLM Subsystem](#23-llm-subsystem)
   - [Provider System](#24-provider-system)
   - [Tool System](#25-tool-system)
   - [Interface / TUI](#26-interface--tui)
   - [Skills](#27-skills)
   - [Runtime](#28-runtime)
   - [Telemetry](#29-telemetry)
   - [Config](#210-config)
   - [Build / Distribution](#211-build--distribution)
   - [Tests](#212-tests)
   - [Documentation](#213-documentation)
3. [Migration Plan](#3-migration-plan)
   - [Phase 0 — Preparation](#phase-0--preparation)
   - [Phase 1 — Core Infrastructure](#phase-1--core-infrastructure)
   - [Phase 2 — Agent & LLM Resilience](#phase-2--agent--llm-resilience)
   - [Phase 3 — Provider Overhaul](#phase-3--provider-overhaul)
   - [Phase 4 — TUI / Interface](#phase-4--tui--interface)
   - [Phase 5 — White-Box Remediation](#phase-5--white-box-remediation)
   - [Phase 6 — Build & Distribution](#phase-6--build--distribution)
   - [Phase 7 — Tests & Validation](#phase-7--tests--validation)
4. [Risk Assessment](#4-risk-assessment)
5. [Files Changed Summary](#5-files-changed-summary)

---

## 1. Repository Overview

| Property | AWS-Hosted (`Esprit`) | Cloud (`Esprit-cloud`) | Opensource (`Esprit-opensource`) |
|---|---|---|---|
| Remote | `junaid-mahmood/Esprit` | `improdead/Esprit` | `improdead/Esprit` |
| Branch | `feat/native-tool-calling` | `main` | `feat/whitebox-remediation-stage` |
| CLI path | `cli/esprit/` | `esprit/` | `esprit/` |
| Backend | Yes (`backend/app/`) | No | No |
| AWS CLI | Yes (`aws-cli/`) | No | No |
| Web frontend | Yes (`web/dist/`) | No | No |
| Source format | `.pyc` bytecode only | `.py` source | `.py` source |
| Version | `0.7.0` | `0.7.0` | `0.7.0` (pyproject) / `0.7.1` (_version.py) |
| Default model | `bedrock/moonshotai.kimi-k2.5` | `bedrock/moonshotai.kimi-k2.5` | `bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| npm package | No | No | Yes (`esprit-cli`) |
| PyInstaller spec | No | No | Yes (`esprit.spec`) |
| Upstream | `usestrix/strix` | None | None |

**Key insight**: The AWS repo's CLI directory (`cli/esprit/`) contains only compiled `.pyc` files from the original Strix fork — no `.py` source files exist. The opensource repo is the authoritative source.

---

## 2. Complete Changelog

### 2.1 Core Renames (Strix → Esprit)

These are systematic across the entire codebase:

| What | Before (AWS) | After (Opensource) |
|---|---|---|
| Package imports | `strix.*` | `esprit.*` |
| Main class | `StrixAgent` | `EspritAgent` |
| TUI app class | `StrixTUIApp` | `EspritTUIApp` |
| Resource helper | `get_strix_resource_path()` | `get_esprit_resource_path()` |
| Sandbox env var | `STRIX_SANDBOX_MODE` | `ESPRIT_SANDBOX_MODE` |
| Config key | `strix_disable_browser` | `esprit_disable_browser` |
| Telemetry refs | `strix.telemetry.*` | `esprit.telemetry.*` |

**Files affected**: Every `.py` file in the codebase (~60+ files).

---

### 2.2 Agent System

#### `esprit/agents/base_agent.py` — Major rewrite

| Change | Details |
|---|---|
| `is_whitebox` propagation | Root agent sets `is_whitebox=bool(self.local_sources)` on `AgentState` |
| LLM auto-resume | Sub-agents automatically retry on transient LLM failures (HTTP 408/429/500/502/503/504) with configurable retry count and cooldown |
| HTTP status extraction | New `_extract_status_code()` extracts HTTP status from LLM error messages |
| Smarter message wakeup | `_should_resume_waiting_on_message()` decides whether an incoming message justifies waking a waiting agent |
| Agent graph integration | Calls `_mark_agent_running()` to update agent graph status |
| Native tool calling | Passes JSON tool schemas via `get_tools_json()` to LLM API instead of XML in system prompt |
| Config integration | Imports `Config` for runtime settings |

#### `esprit/agents/state.py`

| Change | Details |
|---|---|
| New field | `is_whitebox: bool = False` on `AgentState` Pydantic model |

#### `esprit/agents/EspritAgent/system_prompt.jinja` — New file

| Change | Details |
|---|---|
| Jinja template | System prompt is now a Jinja2 template (was inline in AWS) |
| Remediation section | New `VULNERABILITY REMEDIATION` block naming `str_replace_editor` tool explicitly |
| White-box workflow | Adds `WHITE-BOX FIXING IS MANDATORY` critical rule |
| Fixing agent mandate | Updated workflow diagram shows mandatory Fixing Agent step in white-box mode |

#### `esprit/agents/__init__.py`

| Change | Details |
|---|---|
| Exports | Changed from `BaseAgent, AgentState, StrixAgent` to just `EspritAgent` |

---

### 2.3 LLM Subsystem

#### `esprit/llm/llm.py` — Major rewrite

| Change | Details |
|---|---|
| `esprit/` model prefix routing | Models prefixed with `esprit/` are proxied to Bedrock |
| OpenCode fallback chain | On rate-limit errors, automatically tries alternative free OpenCode models |
| Stream idle timeout | `_iter_with_idle_timeout()` aborts stalled LLM streams |
| `_try_opencode_model_fallback()` | Walks a preferred-models list when current model fails |
| `_is_opencode()` | Detects OpenCode provider models |
| `_mask_email()` | Masks email addresses in log output |
| Status code on errors | `LLMRequestFailedError` now carries HTTP `status_code` |
| Removed | `normalize_model_for_litellm()`, `get_provider_api_base()` (replaced by `api_base.py`) |

#### `esprit/llm/api_base.py` — New file (52 lines)

| Function | Purpose |
|---|---|
| `resolve_api_base()` | Centralized API base URL resolution (replaces duplicated 5-env-var fallback chain) |
| `detect_conflicting_provider_base_env()` | Warns when conflicting `*_API_BASE` env vars are set |
| `configured_api_base()` | Returns the currently configured API base |
| `_provider_prefix()` | Maps provider names to env var prefixes |

#### `esprit/llm/cost_estimator.py` — New file (71 lines)

Pre-scan cost estimation utility. Calculates expected token usage and cost based on model pricing.

#### `esprit/llm/pricing.py` — New file (328 lines)

| Class/Function | Purpose |
|---|---|
| `ModelPricing` | Per-model pricing data |
| `PricingDB` | Model pricing database |
| `get_pricing_db()` | Global pricing DB singleton |
| `calculate_cost()` | Calculate cost for token count |
| `get_lifetime_cost()` | Cumulative session cost |
| `add_session_cost()` | Track incremental costs |

#### `esprit/llm/config.py`

| Change | Details |
|---|---|
| Default model | `bedrock/moonshotai.kimi-k2.5` → `bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0` |

#### `esprit/llm/memory_compressor.py`

| Change | Details |
|---|---|
| API base | Uses `resolve_api_base()` instead of inline env var fallback |
| Public functions | `_extract_message_text` → `extract_message_text`, `_summarize_messages` → `summarize_messages` |
| New function | `_resolve_model_for_counting()` |

#### `esprit/llm/dedupe.py`

| Change | Details |
|---|---|
| API base | Uses `resolve_api_base()` instead of inline env var fallback |

---

### 2.4 Provider System

#### `esprit/providers/opencode_zen.py` — New file (74 lines)

Full `OpenCodeZenProvider` class with OAuth flow for OpenCode Zen platform.

#### `esprit/providers/config.py` — Major rewrite

| Change | Details |
|---|---|
| Live model discovery | Queries OpenCode `/models` endpoint with 5-minute cache |
| `get_available_models()` | Replaces static `AVAILABLE_MODELS` dict access |
| `get_public_opencode_models()` | Lists free-tier OpenCode models |
| `is_public_opencode_model()` | Checks if a model is free-tier |
| Model catalog | Expanded to 35+ models for OpenCode |
| Esprit aliases | `default`, `kimi-k2.5`, `kimi-k2`, `haiku` model aliases |
| Removed | `_append_discovered_opencode_models()`, file-based discovery |

#### `esprit/providers/litellm_integration.py`

| Change | Details |
|---|---|
| `zen` alias | Maps to OpenCode provider |
| Public models | No-auth `sk-opencode-public-noauth` key, empty Authorization header |
| Removed | `get_provider_api_base()`, `normalize_model_for_litellm()` (moved to `llm/` and `llm/api_base.py`) |

#### `esprit/providers/opencode_import.py`

| Change | Details |
|---|---|
| Removed | 141 lines of local file-based model discovery (`_discover_models_from_config`, `_discover_models_from_message_history`, etc.) |
| Added | `zen` provider alias |

#### `esprit/providers/commands.py`

| Change | Details |
|---|---|
| Removed | `API_KEY_ONLY_PROVIDERS` concept |
| Improved | Esprit subscription login callback shows email/plan |
| Removed | Hardcoded `opencode` as API-key-only |
| Lazy imports | Cloud-specific credential imports are now lazy |

#### `esprit/providers/esprit_subs.py`

| Change | Details |
|---|---|
| Model aliases | Added `kimi-k2.5`, `kimi-k2` |
| Auth flow | Restructured to use `SupabaseAuthClient` |
| Improved | Cleaner refresh_token, more detailed `modify_request` |

#### `esprit/providers/__init__.py`

| Change | Details |
|---|---|
| New export | `OpenCodeZenProvider` |
| Reordered | `PROVIDER_NAMES` (opencode moved to bottom) |

---

### 2.5 Tool System

#### `esprit/tools/registry.py` — Native tool calling migration

| Change | Details |
|---|---|
| `get_tools_json()` | Returns JSON schemas for LLM native tool calling |
| `_xml_to_json_schema()` | Converts XML schema files to JSON Schema format |
| `_iter_tool_parameters()` | Extracts parameters from XML definitions |
| `_strip_examples_block()` | Cleans example blocks from descriptions |
| `_extract_first_tag_body()` | XML tag content extractor |
| `_parse_tag_attributes()` | XML attribute parser |
| Skills integration | Imports `generate_skills_description` |

#### `esprit/tools/executor.py`

| Change | Details |
|---|---|
| New functions | `_extract_result_from_string()`, `_extract_plain_result()` for result parsing |
| Telemetry | Imports `get_global_tracer` for tool execution tracking |

#### `esprit/tools/__init__.py`

| Change | Details |
|---|---|
| Env var | `STRIX_SANDBOX_MODE` → `ESPRIT_SANDBOX_MODE` |
| Config key | `strix_disable_browser` → `esprit_disable_browser` |
| New export | `get_tools_json` |

#### `esprit/tools/context.py` — New file (13 lines)

Agent context tracking: `get_current_agent_id()`, `set_current_agent_id()`.

#### `esprit/tools/agents_graph/agents_graph_actions.py`

| Change | Details |
|---|---|
| `_snapshot_inherited_messages()` | Safe tool_use/tool_result sequencing for inherited context |
| `is_whitebox` propagation | Sub-agents inherit `is_whitebox` from parent |
| `deepcopy` | Messages are deep-copied during inheritance |
| `_format_messages_as_text()` | Text formatter for inherited messages |
| `_format_messages_brief()` | Brief formatter for message snapshots |
| `_summarize_inherited_context()` | Context summarization for sub-agents |

#### `esprit/tools/finish/finish_actions.py`

| Change | Details |
|---|---|
| `_check_remediation_completeness()` | Soft-gates `finish_scan` in white-box mode. Compares the number of finished fixing agents against the number of reported vulnerabilities. If `fix_count >= vuln_count` the gate passes silently. If coverage is partial or zero, the gate bounces with a message detailing how many vulns remain unfixed. |
| Per-scan bounce counter | `_remediation_bounce_counts: dict[str, int]` keyed by root agent ID. After `_MAX_REMEDIATION_BOUNCES` (2) bounces for a given scan, the gate allows through with a log warning — this is a deliberate escape hatch to avoid deadlocking the agent loop, not full enforcement. |
| Module-level logger | Replaced inline `import logging` statements |

> **Known limitation**: The gate matches fixing agents by checking for `"fix"` in the agent name (case-insensitive). It does not track which specific vulnerability each fixer addresses — a fixer named for vuln A satisfies coverage for vuln B. The bounce limit also means a determined agent can bypass the gate after 2 retries per scan.

#### `esprit/tools/reporting/reporting_actions.py`

| Change | Details |
|---|---|
| Deduplication | Imports `esprit.llm.dedupe` for finding deduplication |
| Telemetry | Imports `get_global_tracer` |

#### New tool manager files (extracted from monolithic actions)

| File | Lines | Purpose |
|---|---|---|
| `tools/browser/browser_instance.py` | 582 | `BrowserInstance`, `_BrowserState` — browser lifecycle |
| `tools/browser/tab_manager.py` | 362 | `BrowserTabManager` — multi-tab management |
| `tools/proxy/proxy_manager.py` | 798 | `ProxyManager` — MITM proxy lifecycle |
| `tools/python/python_instance.py` | 175 | `PythonInstance` — Python REPL session |
| `tools/python/python_manager.py` | 144 | `PythonSessionManager` — multi-session management |
| `tools/terminal/terminal_manager.py` | 163 | `TerminalManager` — terminal session management |
| `tools/terminal/terminal_session.py` | 448 | `TerminalSession`, `BashCommandStatus` |

#### `esprit/tools/web_search/` — New tool (entire directory)

| File | Purpose |
|---|---|
| `web_search_actions.py` | Web search tool actions |
| `web_search_actions_schema.xml` | Tool schema |

#### XML Schema files — All new in opensource (12 files)

AWS has zero XML schema files. All `*_actions_schema.xml` files are new:
`agents_graph`, `browser`, `file_edit`, `finish`, `notes`, `proxy`, `python`, `reporting`, `terminal`, `thinking`, `todo`, `web_search`.

---

### 2.6 Interface / TUI

#### `esprit/interface/main.py` — Most heavily modified file

| New Function | Purpose |
|---|---|
| `ensure_provider_configured()` | Interactive provider auth flow |
| `pre_scan_setup()` | Pre-flight checks before scan |
| `_is_paid_subscription_plan()` | Subscription tier detection |
| `_build_targets_info()` | Target info display |
| `_get_configured_providers()` | List configured providers |
| `cmd_uninstall()` | CLI uninstall command |
| `_get_available_models()` | Available model listing |
| `_apply_launchpad_result()` | Apply launchpad wizard selections |
| `ensure_docker_running()` | Docker availability check |
| `_is_cloud_subscription_model()` | Cloud model detection |
| `display_cost_estimate()` | Pre-scan cost display |
| `_should_use_cloud_runtime()` | Runtime selection logic |
| Updater integration | `apply_update`, `has_pending_update` hooks |
| `spd` alias | Alternative CLI entry point |
| OpenCode fallback | Warm-up with model fallback chain |

#### `esprit/interface/tui.py` — Major rewrite (~2270 lines changed)

| Change | Details |
|---|---|
| Theme tokens | Full theme integration throughout all widgets |
| `VulnerabilityOverlayScreen` | Dedicated vuln workspace (list/detail/copy) — Ctrl+V |
| `AgentHealthPopupScreen` | Agent health monitoring — Ctrl+H |
| `UpdateScreen` | In-app update flow — Ctrl+U |
| `BrowserPreviewScreen` | Browser screenshot preview |
| Splash animation | Wordmark sweep effect with themed colors |
| Quarter-block rendering | `_render_wordmark_quarter_block()` |
| Emoji removal | All emojis replaced with `[marker]` text labels |
| Cost display | Live cost tracking in stats panel |

#### New interface files

| File | Lines | Purpose |
|---|---|---|
| `interface/launchpad.py` | 1328 | Interactive project setup wizard with theme selection, provider config, directory suggester |
| `interface/image_protocol.py` | 82 | Terminal image protocol detection (Kitty TGP, Sixel) |
| `interface/image_renderer.py` | 349 | 5-tier image rendering cascade: Kitty TGP → Sixel → halfcell → quarter-block → half-block |
| `interface/image_widget.py` | 154 | Textual widget for browser screenshots |
| `interface/theme_tokens.py` | 438 | 5-theme system (esprit/ember/matrix/glacier/crt) with marker colors and style resolution |
| `interface/updater.py` | 147 | Self-update mechanism (check/download/apply) |
| `interface/assets/launchpad_styles.tcss` | ~255 | Textual CSS for launchpad |
| `interface/assets/tui_styles.tcss` | ~1100 | Textual CSS for TUI (major rework) |

#### `esprit/interface/utils.py`

| Change | Details |
|---|---|
| Theme tokens | Integrated throughout |
| `_resolve_session_cost()` | Token-based cost calculation with fallback |
| `_estimate_projection()` | Projected cost/duration |
| `_format_elapsed()` | Elapsed time formatting |
| `build_subscription_quota_lines()` | Subscription quota display |
| Emoji removal | Replaced with `[marker]` text labels |

#### Tool component renderers (theme integration)

Files modified: `browser_renderer.py`, `reporting_renderer.py`, `thinking_renderer.py`, `todo_renderer.py`, `web_search_renderer.py` — all updated for theme token support and emoji removal.

---

### 2.7 Skills

#### `esprit/skills/coordination/root_agent.md` — Modified

| Change | Details |
|---|---|
| Fixing Agents | Added reference in hierarchical delegation |
| Remediation Stage | New section for white-box mode with `create_agent` examples |
| Completion | Updated to include white-box remediation check |

#### `esprit/skills/vulnerabilities/remediation.md` — New file (234 lines)

Remediation skill document teaching Fixing Agents to use `str_replace_editor` for vulnerability patching. Covers SQLi, XSS, IDOR, SSRF, RCE, path traversal, CSRF, JWT fix patterns.

#### All other skill `.md` files — New in opensource (42 additional, 43 total including remediation above)

AWS has zero non-Python skill files. All `.md` skill content is new:
- **cloud/** (4): aws, azure, gcp, kubernetes
- **coordination/** (1): root_agent
- **frameworks/** (6): django, express, fastapi, nextjs, rails, spring_boot
- **protocols/** (4): graphql, grpc, oauth, websocket
- **reconnaissance/** (5): osint, port_scanning, subdomain_enumeration, technology_fingerprinting, web_content_discovery
- **scan_modes/** (3): deep, quick, standard
- **technologies/** (2): firebase_firestore, supabase
- **vulnerabilities/** (17, excluding remediation listed above): authentication_jwt, broken_function_level_authorization, business_logic, csrf, idor, information_disclosure, insecure_file_uploads, mass_assignment, open_redirect, path_traversal_lfi_rfi, race_conditions, rce, sql_injection, ssrf, subdomain_takeover, xss, xxe

---

### 2.8 Runtime

#### `esprit/runtime/__init__.py` — Modified

| Change | Details |
|---|---|
| Cloud runtime | `get_runtime()` now returns `CloudRuntime` when `esprit_runtime_backend == "cloud"` |

#### `esprit/runtime/cloud_runtime.py` — New file (148 lines)

`CloudRuntime` class — API-based cloud sandbox that connects to Esprit Cloud ECS containers instead of local Docker.

#### `esprit/runtime/tool_server.py` — New file (166 lines)

FastAPI tool execution server for sandbox mode.

#### `esprit/runtime/docker_runtime.py`

| Change | Details |
|---|---|
| Telemetry | Imports `get_global_tracer` for container lifecycle tracking |

---

### 2.9 Telemetry

#### `esprit/telemetry/posthog.py`

| Change | Details |
|---|---|
| Non-blocking sends | `_send()` fires in daemon thread |
| Timeout | Reduced from 10s to 5s |

#### `esprit/telemetry/tracer.py`

| Change | Details |
|---|---|
| `_set_run_status()` | Proper status tracking throughout agent lifecycle |
| `_cache_metrics()` | New function for metric caching |
| Final status inference | Cleanup logic infers final status from root agent states |

---

### 2.10 Config

#### `esprit/config/config.py`

| Change | Details |
|---|---|
| `get_launchpad_theme()` | Theme persistence (read) |
| `save_launchpad_theme()` | Theme persistence (write) |
| `apply_saved()` | Fixed to preserve non-env sections in config.json |
| `save_current()` | Fixed to preserve non-env sections in config.json |

---

### 2.11 Build / Distribution

#### `package.json` — New (npm distribution)

npm wrapper for `esprit-cli` with `bin/esprit.js` entry point, postinstall script, published to npm registry.

#### `esprit.spec` — New (PyInstaller)

Cross-platform binary bundling config. Bundles `.jinja`, `*_schema.xml`, `.tcss`, skills `.md`, and LiteLLM data. Excludes sandbox-only deps and dev tools.

#### `start.sh` — Modified

| Change | Details |
|---|---|
| Poetry sync | Auto-runs `poetry install --no-interaction --quiet` before starting |
| Guard | Only enters poetry path if `pyproject.toml` exists |
| Update checker | `_check_update()` queries GitHub releases API (3-second timeout) |
| Color output | Terminal color variables for feedback |

#### `scripts/install.sh` — Modified

| Change | Details |
|---|---|
| `--force` / `-f` flag | Allows reinstallation of same version |
| Platform names | `darwin` → `macos` in target triplets |
| Versioned filenames | Archive names include version (`esprit-VERSION-target`) |
| Temp cleanup trap | `trap _cleanup_tmp EXIT INT TERM HUP` |
| Docker pull strategy | Always attempts pull for latest, falls back to cached |
| Error tolerance | Docker failures return 0 (non-fatal) instead of 1 |

#### `containers/docker-entrypoint.sh` — Modified

| Change | Details |
|---|---|
| Log cleanup | Removes `$CAIDO_LOG` and `$TOOL_SERVER_LOG` after services are healthy |

#### `.github/workflows/build-release.yml` — Modified

| Change | Details |
|---|---|
| Binary naming | Binary inside archive is just `esprit` (no version/target suffix) |
| Cleanup | Removes intermediate binary after archiving |

#### `.gitignore` — Modified

| Change | Details |
|---|---|
| New entries | `.mypy_cache/`, `.ruff_cache/` |

#### `pyproject.toml` — Modified

| Change | Details |
|---|---|
| `spd` alias | New entry point (`esprit.interface.main:main`) |
| `httpx` | Promoted from optional to required |
| `textual-image` | Promoted from optional extra to required |
| Removed | `[tool.poetry.extras]` section for `enhanced-preview` |

#### GUI static assets — New

| File | Purpose |
|---|---|
| `gui/static/app.js` | Dashboard JavaScript (stop/retry buttons) |
| `gui/static/index.html` | Dashboard HTML |
| `gui/static/style.css` | Dashboard CSS |

#### `gui/server.py` — Modified

| Change | Details |
|---|---|
| New endpoints | `/api/agent/{agent_id}/stop`, `/api/agent/{agent_id}/retry` |

---

### 2.12 Tests

#### New test files in opensource (16)

| File | Covers |
|---|---|
| `tests/config/test_config_launchpad_theme.py` | Theme persistence |
| `tests/interface/test_image_renderer.py` | Image rendering pipeline |
| `tests/interface/test_launchpad_alignment_styles.py` | Launchpad layout |
| `tests/interface/test_launchpad_model_config.py` | Model config UI |
| `tests/interface/test_launchpad_paths.py` | Directory suggester |
| `tests/interface/test_launchpad_provider_config.py` | Provider config UI |
| `tests/interface/test_launchpad_theme.py` | Theme system |
| `tests/interface/test_main_provider_setup.py` | Provider setup flow |
| `tests/interface/test_tool_renderer_labels.py` | Marker labels |
| `tests/interface/test_tui_input_alignment_styles.py` | TUI input alignment |
| `tests/interface/test_tui_status_indicator.py` | Status indicators |
| `tests/interface/test_tui_theme_tokens.py` | Theme tokens |
| `tests/llm/test_api_base.py` | `resolve_api_base()` |
| `tests/providers/test_config_routes.py` | OpenCode route config |
| `tests/providers/test_opencode_zen.py` | OpenCode Zen provider |
| `tests/tools/test_finish_remediation.py` | Remediation gate coverage, per-scan bounce counter, skills format |

#### AWS-only test files (not in opensource)

These files exist only in the AWS or Cloud repos. During migration, keep them alongside the opensource tests.

**In `junaid-mahmood/Esprit` (AWS repo — 3 files):**

| File | Why excluded |
|---|---|
| `tests/auth/test_credentials.py` | Platform-specific auth |
| `tests/runtime/test_cloud_runtime.py` | Cloud runtime (ECS) tests |
| `tests/interface/test_main_cloud_runtime.py` | Cloud runtime integration |

**In `Esprit-cloud` only (6 files/dirs — not in either the AWS or opensource repos):**

| File | Why excluded |
|---|---|
| `tests/test_client/` | Cloud infrastructure client tests |
| `tests/test_server/` | Cloud infrastructure server tests |
| `tests/runtime/test_runtime_backend.py` | Runtime backend selection |
| `tests/interface/test_subscription_quota.py` | Subscription plan tests |
| `tests/providers/test_esprit_subs.py` | Esprit subs provider |
| `tests/providers/test_opencode_import.py` | Old file-based discovery (replaced by live endpoint) |

#### Modified test files (8)

| File | Changes |
|---|---|
| `tests/agents/test_base_agent.py` | Adds `TestWaitingResumePolicy` (11 tests), `TestLLMAutoResumePolicy` (6 tests) |
| `tests/gui/test_tracer_bridge.py` | 2 new tests for status fallback |
| `tests/interface/test_browser_preview.py` | Updated for `textual-image` as required dep |
| `tests/interface/test_stats_panel.py` | Cost display, marker labels, projection tests (~130 lines) |
| `tests/llm/test_llm.py` | Status code propagation, idle timeout, OpenCode fallback (~130 lines) |
| `tests/providers/test_litellm_integration.py` | OpenCode prefix, zen alias, public model tests |
| `tests/telemetry/test_tracer.py` | `_set_run_status` tracking tests |
| `tests/tools/test_context_summarization.py` | Tool metadata preservation, snapshot tests (~115 lines) |

---

### 2.13 Documentation

#### New docs in opensource

| File | Purpose |
|---|---|
| `docs/multi-model-routing.md` | Multi-model routing design (untracked) |
| `docs/vulnerability-remediation-design.md` | Remediation gap analysis and design |
| `docs/remediation-implementation-plan.md` | Implementation plan with OpenCode comparison |
| `telemetry/README.md` | Telemetry documentation |

#### Modified docs

| File | Changes |
|---|---|
| `docs/index.mdx` | Install URL changed to raw GitHub URL |
| `docs/integrations/ci-cd.mdx` | Install URL updated |
| `docs/integrations/github-actions.mdx` | Install URL updated |
| `docs/llm-providers/overview.mdx` | Added MiniMax M2.5 Free, OpenCode public models section |
| `docs/quickstart.mdx` | Docker optional for cloud, npm install tab, `esprit scan` syntax, OpenCode section |
| `docs/usage/cli.mdx` | `esprit scan` syntax, launchpad section, `esprit provider` commands |
| `README.md` | Complete rewrite: 296-line comprehensive README (was 6-line Mintlify stub) |

#### AWS-only docs

| File | Notes |
|---|---|
| `docs/media/esprit-launch.mp4` | 43MB product demo video (keep for website) |

---

## 3. Migration Plan

### Prerequisites

- Ensure the AWS repo has no in-flight PRs that would conflict
- Create a backup branch: `git checkout -b backup/pre-migration-$(date +%Y%m%d)`
- Verify the backend (`backend/app/`) and AWS CLI (`aws-cli/`) are not affected by CLI changes

### Phase 0 — Preparation

**Goal**: Set up the merge environment.

1. In the AWS repo, ensure the `main` branch is up to date
2. Create a new branch: `git checkout -b feat/migrate-to-opensource`
3. Verify the `cli/` directory structure — the `.pyc` files need to be replaced with `.py` source
4. Remove all `.pyc` / `__pycache__` directories under `cli/`

**Risk**: Low. Only removes compiled artifacts.

### Phase 1 — Core Infrastructure (Strix → Esprit rename + new files)

**Goal**: Replace `cli/esprit/` with the opensource `esprit/` source.

**Strategy**: Since the AWS CLI directory only has `.pyc` bytecode files and the opensource repo has the full `.py` source, the cleanest approach is a wholesale replacement:

1. Delete `cli/esprit/` entirely (it only has `.pyc` files)
2. Copy `esprit/` from opensource into `cli/esprit/`
3. Update any import paths in `backend/` or `aws-cli/` that reference `strix.*` → `esprit.*`
4. Update `STRIX_SANDBOX_MODE` → `ESPRIT_SANDBOX_MODE` in:
   - `containers/Dockerfile`
   - `containers/docker-entrypoint.sh`
   - Any deployment scripts
5. Copy root-level files: `pyproject.toml`, `Makefile`, `start.sh`, `scripts/install.sh`, `.gitignore`
6. Copy `containers/docker-entrypoint.sh` (log cleanup change)

**Considerations**:
- The AWS repo has `backend/` and `aws-cli/` directories that import from the CLI — check for `strix.*` references
- The AWS backend may have its own models/services that reference strix naming
- Keep `esprit.spec` and `package.json` only if npm/binary distribution is desired for AWS

**Risk**: Medium. The wholesale replacement is clean but requires verifying backend/aws-cli compatibility.

### Phase 2 — Agent & LLM Resilience

**Goal**: Bring agent and LLM improvements to AWS.

Already included in Phase 1 (files are copied), but verify:

1. `base_agent.py` — LLM auto-resume works with AWS's LLM provider setup
2. `llm/llm.py` — OpenCode fallback chain may not be relevant for AWS (Bedrock-only)
3. `llm/config.py` — Verify default model is appropriate for AWS deployment
4. `llm/api_base.py` — `resolve_api_base()` should work with AWS env vars

**AWS-specific adjustments**:
- If AWS always uses Bedrock, the OpenCode fallback chain is harmless (never triggers)
- The default model (`claude-haiku-4-5`) may need to match AWS's Bedrock model catalog
- Verify `ESPRIT_API_BASE` env var is set correctly in ECS task definitions

**Risk**: Low. Changes are additive and backward-compatible.

### Phase 3 — Provider Overhaul

**Goal**: Update provider system with live model discovery and public model support.

Already included in Phase 1. Verify:

1. `providers/config.py` — Live `/models` endpoint may not be accessible from AWS VPC
2. `providers/litellm_integration.py` — Public OpenCode no-auth behavior shouldn't affect AWS
3. `providers/esprit_subs.py` — `SupabaseAuthClient` must be available

**AWS-specific adjustments**:
- If AWS uses only Esprit Cloud subscription models, the OpenCode provider changes are irrelevant
- Verify Supabase auth endpoint is reachable from ECS

**Risk**: Low. Provider system is modular; unused providers are inert.

### Phase 4 — TUI / Interface

**Goal**: Bring UI improvements to AWS.

Already included in Phase 1. Verify:

1. `interface/launchpad.py` — Works in headless/ECS mode (should be CLI-only)
2. `interface/tui.py` — Theme system doesn't break existing rendering
3. `interface/image_renderer.py` — Terminal protocol detection works in AWS terminal
4. `.tcss` stylesheets — Bundled correctly in deployment

**AWS-specific adjustments**:
- If AWS runs headless (no TUI), the launchpad and theme changes are safe (only activate in interactive mode)
- Verify `.tcss` files are included in the deployment artifact

**Risk**: Low. TUI code only runs in interactive terminals.

### Phase 5 — White-Box Remediation

**Goal**: Enable vulnerability patching in white-box scans.

Already included in Phase 1. Verify:

1. `finish_actions.py` — `_check_remediation_completeness()` soft-gates `finish_scan`. It compares the count of finished fixing agents against reported vulnerabilities and bounces the agent if coverage is incomplete. After 2 bounces per scan it allows through (escape hatch). It does **not** verify which specific vulnerabilities each fixer addressed.
2. `system_prompt.jinja` — Jinja template is rendered correctly at runtime. Skills format uses `skills="remediation,<vuln_type>"` (comma-separated string, no brackets).
3. `skills/vulnerabilities/remediation.md` — Skill is discovered and loaded
4. `is_whitebox` propagation — Root agent → sub-agents

**Risk**: Low. Remediation gate only activates in white-box mode.

### Phase 6 — Build & Distribution

**Goal**: Update build and deployment configs.

1. Copy `.github/workflows/build-release.yml` (binary naming fix)
2. Update `scripts/install.sh` (platform names, force flag, temp cleanup)
3. Update `start.sh` (poetry sync, update checker)
4. Update `containers/docker-entrypoint.sh` (log cleanup)
5. Optionally add `esprit.spec` for PyInstaller builds
6. Optionally add `package.json` for npm distribution

**AWS-specific adjustments**:
- The GitHub Actions workflow targets `improdead/Esprit` — update repo references for AWS
- Install script points to `improdead/Esprit` GitHub releases — update for AWS deployment
- Docker image name may differ between repos

**Risk**: Medium. Build configs contain repo-specific URLs.

### Phase 7 — Tests & Validation

**Goal**: Ensure all tests pass and no regressions.

1. Copy `tests/` from opensource
2. Keep AWS-only tests (`tests/auth/`, `tests/test_client/`, `tests/test_server/`, `tests/runtime/test_cloud_runtime.py`, etc.)
3. Run full test suite: `poetry run pytest tests/ -v`
4. Verify test count matches or exceeds AWS's current count
5. Run linting: `make lint`
6. Run type checking: `make type-check`
7. Manual smoke tests:
   - `esprit scan <target>` from CLI
   - TUI launch and theme switching
   - Provider configuration flow
   - Docker sandbox creation
   - White-box scan with local source path

**Risk**: Low. Tests are additive.

---

## 4. Risk Assessment

| Risk | Severity | Mitigation |
|---|---|---|
| Backend/AWS-CLI `strix.*` imports break | **High** | Search `backend/` and `aws-cli/` for `strix` references; update before deploying |
| ECS task definitions reference `STRIX_*` env vars | **High** | Audit all ECS task defs, CloudFormation/Terraform templates |
| Default model change breaks AWS billing | **Medium** | Verify `claude-haiku-4-5` is in AWS Bedrock model catalog; update if needed |
| OpenCode `/models` endpoint unreachable from VPC | **Low** | Only affects provider discovery; falls back gracefully |
| `.tcss` files missing from deployment | **Medium** | Verify deployment artifact includes non-Python files |
| Install script URLs point to wrong repo | **Medium** | Update GitHub URLs in `install.sh` and `start.sh` |
| Docker image name mismatch | **Medium** | Verify `ESPRIT_IMAGE` env var matches actual Docker Hub image |

---

## 5. Files Changed Summary

### By category

| Category | New | Modified | Removed | Total |
|---|---|---|---|---|
| Agents | 2 | 3 | 1 (renamed) | 6 |
| LLM | 3 | 4 | 0 | 7 |
| Providers | 2 | 6 | 0 | 8 |
| Tools | 10 + 12 schemas | 6 | 0 | 28 |
| Interface | 8 | 8 | 0 | 16 |
| Skills | 43 | 1 | 0 | 44 |
| Runtime | 2 | 2 | 0 | 4 |
| Telemetry | 1 | 2 | 0 | 3 |
| Config | 0 | 1 | 0 | 1 |
| GUI | 3 | 1 | 0 | 4 |
| Build/Deploy | 3 | 6 | 0 | 9 |
| Tests | 16 | 8 | 0 | 24 |
| Docs | 4 | 6 | 0 | 10 |
| **Total** | **~109** | **~54** | **1** | **~164** |

### Critical path files (touch these first)

1. `esprit/agents/base_agent.py` — Core agent loop
2. `esprit/llm/llm.py` — LLM orchestration
3. `esprit/tools/registry.py` — Tool loading
4. `esprit/tools/__init__.py` — Env var rename
5. `esprit/interface/main.py` — Entry point
6. `containers/docker-entrypoint.sh` — Container startup
7. `pyproject.toml` — Dependencies

### Files safe to copy verbatim

- All `skills/*.md` files (pure content, no code)
- All `*_schema.xml` files (pure schemas)
- All `interface/assets/*.tcss` files (pure styles)
- All new test files (additive)
- All new doc files (additive)
- `esprit.spec`, `package.json` (new files)
