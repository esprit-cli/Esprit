# Esprit Orchestration System (ASCII Deep Dive)

This document maps how the Esprit orchestration AI system works end-to-end, including:
- runtime orchestration flow
- tool routing and execution behavior
- skill loading and usage
- scan mode behavior
- all registered tools and all skill files

Scope analyzed from code in:
- /Users/dekai/Documents/esprit/Esprit/esprit
- /Users/dekai/Documents/esprit/Esprit/docs

Notes:
- `Esprit-cloud` and `Esprit-opensource` are close siblings. This document uses `/Users/dekai/Documents/esprit/Esprit` as canonical.
- Paths below are workspace-relative to `/Users/dekai/Documents/esprit/Esprit`.

---

## 1) System Topology (High Level)

```text
+---------------------------------------------------------------+
|                          User Interface                       |
|  - CLI (non-interactive)                                      |
|  - TUI (interactive)                                          |
+------------------------------+--------------------------------+
                               |
                               v
+---------------------------------------------------------------+
|                    interface/main.py bootstrap                |
|  - parse args, target typing, scan mode                       |
|  - provider/model pre-check                                   |
|  - docker checks + sandbox image pull                         |
|  - run_name, local/repo source collection                     |
+------------------------------+--------------------------------+
                               |
                               v
+---------------------------------------------------------------+
|                         Root EspritAgent                      |
|  - BaseAgent loop                                              |
|  - LLM stream + tool loop                                      |
|  - root skill injected                                          |
|  - scan_mode skill injected                                     |
+------------------------------+--------------------------------+
                               |
                               v
+---------------------------------------------------------------+
|                     Agents Graph Orchestration                |
|  create_agent / wait_for_message / send_message / finish      |
|  - parent-child graph                                           |
|  - thread-per-subagent                                          |
|  - inherited context summary                                    |
+------------------------------+--------------------------------+
                               |
                               v
+---------------------------------------------------------------+
|                         Tool Execution Plane                  |
|  execute_tool() decides local vs sandbox                        |
|                                                                 |
|  sandbox_execution=True -> sandbox tool_server /execute         |
|  sandbox_execution=False -> local orchestrator process          |
+------------------------------+--------------------------------+
                               |
                               v
+---------------------------------------------------------------+
|                         Persistence + Telemetry               |
|  tracer stores:                                                 |
|  - vuln reports (md + csv)                                      |
|  - final penetration report                                     |
|  - agent/tool run metadata                                      |
+---------------------------------------------------------------+
```

---

## 2) Startup and Session Flow

Primary files:
- `esprit/interface/main.py`
- `esprit/interface/cli.py`
- `esprit/interface/tui.py`
- `esprit/interface/utils.py`

### 2.1 Boot sequence

```text
main()
  -> parse_arguments()
  -> apply config override (optional)
  -> launchpad (if no explicit scan target)
  -> check_docker_installed()
  -> ensure_docker_running()
  -> pull_docker_image() if missing
  -> pre_scan_setup() [provider + model readiness]
  -> validate_environment()
  -> warm_up_llm() [skips for codex oauth and antigravity]
  -> generate_run_name()
  -> clone repository targets (if any)
  -> collect local sources
  -> start telemetry session
  -> run_cli() or run_tui()
```

### 2.2 Target typing decision

Implemented in `infer_target_type()` in `esprit/interface/utils.py`.

```text
Input target string
  |
  +-- git@... or git://... ----------------------> repository
  |
  +-- http/https URL
  |      +-- *.git or git endpoint probe --------> repository
  |      +-- otherwise --------------------------> web_application
  |
  +-- valid IP ----------------------------------> ip_address
  |
  +-- existing local directory ------------------> local_code
  |
  +-- domain-like host --------------------------> web_application (https://...)
  |
  +-- else -> invalid target
```

### 2.3 White-box vs black-box context

- If local sources or cloned repo exist, agent gets white-box/combined context.
- If only URLs/IPs, agent executes black-box style.
- Localhost URLs are rewritten to host gateway for container access (`host.docker.internal`).

---

## 3) Runtime and Sandbox Layer

Primary files:
- `esprit/runtime/__init__.py`
- `esprit/runtime/docker_runtime.py`
- `esprit/runtime/tool_server.py`

### 3.1 Runtime backend

```text
get_runtime()
  if ESPRIT_RUNTIME_BACKEND == docker -> DockerRuntime
  else -> unsupported
```

### 3.2 Sandbox model

- One scan container is reused per scan id.
- Tool server runs inside container on port `48081` with bearer token.
- Local code/repo sources copied into `/workspace/<workspace_subdir>`.
- Each agent registers with tool server, but agents share the same container workspace.

```text
Root/Subagent needs sandbox
  -> DockerRuntime.create_sandbox(agent_id,...)
     -> find/reuse/create scan container
     -> wait_for_tool_server(/health)
     -> register_agent(/register_agent)
     -> return sandbox_info(token, port, workspace_id)
```

### 3.3 Tool server execution contract

`tool_server.py` (sandbox-only process):
- endpoint: `POST /execute`
- validates bearer token
- executes tool by name and kwargs
- one active task per agent id (new task cancels old task)
- hard timeout enforced per request

---

## 4) Agent Orchestration Core

Primary files:
- `esprit/agents/EspritAgent/esprit_agent.py`
- `esprit/agents/base_agent.py`
- `esprit/agents/state.py`
- `esprit/tools/agents_graph/agents_graph_actions.py`

### 4.1 Root agent creation

- `EspritAgent` extends `BaseAgent`.
- If `parent_id is None`, default skill list includes `root_agent`.
- Root then executes `agent_loop(task_description)`.

### 4.2 Agent loop state machine

```text
while True:
  if force_stop -> waiting
  read inter-agent/user messages
  if waiting_for_input -> wait/poll
  if should_stop/completed/max_iter -> return or waiting
  if llm_failed -> wait for user input

  iteration += 1
  add max-iteration warnings near limit

  process_iteration():
    - gather tools (native if supported)
    - stream LLM response
    - parse tool invocations
    - append assistant message
    - execute tools
    - if finish tool says done -> completed
```

### 4.3 Agents graph and subagent model

Global orchestration state in `agents_graph_actions.py`:
- `_agent_graph` (nodes + edges)
- `_agent_instances`
- `_agent_states`
- `_agent_messages`
- `_running_agents`
- `_root_agent_id`

Subagent creation via `create_agent(...)`:
1. validate skill names and max 5 skills
2. inherit timeout/scan_mode from parent
3. create new `AgentState(parent_id=...)`
4. launch subagent in dedicated thread
5. optional inherited context (summarized when long)

### 4.4 Inter-agent messaging

- `send_message_to_agent` sends structured messages with priority/type.
- `wait_for_message` marks agent waiting.
- `BaseAgent._check_agent_messages` injects unread messages into agent conversation.
- waiting agents auto-resume when messages arrive or timeout is reached.

### 4.5 Finish semantics

- Subagents must call `agent_finish`.
- Root must call `finish_scan`.
- `finish_scan` rejects completion if any other agents are still `running` or `stopping`.

---

## 5) LLM Layer and Tool Calling

Primary files:
- `esprit/llm/llm.py`
- `esprit/llm/utils.py`
- `esprit/llm/memory_compressor.py`
- `esprit/providers/antigravity_format.py`

### 5.1 Prompt construction stack

```text
system_prompt.jinja
  + injected tools prompt (XML fallback mode only)
  + loaded skills content
  + scan mode skill
  + agent identity metadata message
  + compressed conversation history
```

### 5.2 Native vs XML tool invocation

```text
if model supports native function calling:
  - pass JSON tool schemas to provider
  - parse native tool calls from provider response
else:
  - model emits XML tags: <function=...><parameter=...>
  - parse with regex parser in llm/utils.py
```

### 5.3 Antigravity path

Antigravity models bypass standard LiteLLM stream path:

```text
OpenAI-style messages/tools
  -> antigravity_format conversion (Google GenAI shape)
  -> Cloud Code SSE endpoint
  -> parse_sse_chunk
  -> normalized tool calls back to Esprit format
```

### 5.4 Memory compression

- Keeps recent messages (`MIN_RECENT_MESSAGES = 15`) verbatim.
- Summarizes older chunks when token threshold exceeded.
- Preserves critical security context and tool outcomes.

---

## 6) Tool Routing and Execution Internals

Primary files:
- `esprit/tools/__init__.py`
- `esprit/tools/registry.py`
- `esprit/tools/executor.py`
- `esprit/tools/argument_parser.py`

### 6.1 Registration and schemas

- Tools are registered via `@register_tool`.
- XML schema loaded from `*_schema.xml`.
- JSON schema auto-derived for native function calling.
- Dynamic skill list is injected into `create_agent` schema text.

### 6.2 Execution decision

```text
execute_tool(tool_name,...)
  if should_execute_in_sandbox(tool_name) and not ESPRIT_SANDBOX_MODE:
      -> call sandbox tool server over HTTP
  else:
      -> execute local function directly
```

### 6.3 Conversation result encoding

`process_tool_invocations()` supports two histories:

- Native mode: each tool result appended as role=`tool` with `tool_call_id`.
- Legacy mode: all tool results packed into one role=`user` message with XML wrappers.

### 6.4 Screenshot/image handling

- Browser tool results may include base64 screenshot.
- Executor extracts screenshot into image content parts and sanitizes textual payload.

---

## 7) Scan Mode Behavior

Files:
- `esprit/skills/scan_modes/quick.md`
- `esprit/skills/scan_modes/standard.md`
- `esprit/skills/scan_modes/deep.md`
- `docs/usage/scan-modes.mdx`

### 7.1 Quick
- Time-boxed, high-impact checks.
- Breadth over depth.
- Minimum parallel branches for auth/access, injection, SSRF/secrets.

### 7.2 Standard
- Balanced systematic coverage.
- Full attack surface mapping, then structured parallel testing lanes.

### 7.3 Deep
- Exhaustive recon + deep chaining.
- Maximum parallel specialization and full exploit chain pursuit.

---

## 8) Complete Tool Catalog (All 32 Tools)

Source schemas:
- `esprit/tools/*/*_schema.xml`

### 8.1 agents_graph tools
1. `agent_finish`
- Purpose: mark subagent complete and optionally report to parent.
- Params: `result_summary` (req), `findings`, `success`, `report_to_parent`, `final_recommendations`.

2. `create_agent`
- Purpose: spawn specialized async subagent.
- Params: `task` (req), `name` (req), `inherit_context`, `skills` (comma list, max 5).

3. `send_message_to_agent`
- Purpose: inter-agent coordination.
- Params: `target_agent_id` (req), `message` (req), `message_type`, `priority`.

4. `view_agent_graph`
- Purpose: inspect graph topology/status.
- Params: none.

5. `wait_for_message`
- Purpose: pause agent until message/timeout.
- Params: `reason`.

### 8.2 browser tools
6. `browser_action`
- Purpose: Playwright browser operations.
- Main action families:
  - navigation: launch/goto/back/forward
  - interactions: click/type/double_click/hover/press_key/scroll
  - tabs: new_tab/switch_tab/close_tab/list_tabs
  - utilities: wait/execute_js/save_pdf/get_console_logs/view_source/close
- Key params: `action` (req), `url`, `coordinate`, `text`, `tab_id`, `js_code`, `duration`, `key`, `file_path`, `clear`.

### 8.3 file_edit tools
7. `str_replace_editor`
- Purpose: openhands file editor abstraction.
- Params: `command` (req), `path` (req), and command-specific args.

8. `list_files`
- Purpose: list directory files/dirs.
- Params: `path` (req), `recursive`.

9. `search_files`
- Purpose: regex search (ripgrep-backed).
- Params: `path` (req), `regex` (req), `file_pattern`.

### 8.4 finish tools
10. `finish_scan`
- Purpose: final root scan completion payload.
- Params (all req): `executive_summary`, `methodology`, `technical_analysis`, `recommendations`.

### 8.5 notes tools
11. `create_note`
- Params: `title` (req), `content` (req), `category`, `tags`.

12. `list_notes`
- Params: `category`, `tags`, `search`.

13. `update_note`
- Params: `note_id` (req), `title`, `content`, `tags`.

14. `delete_note`
- Params: `note_id` (req).

### 8.6 proxy tools
15. `list_requests`
- Params: `httpql_filter`, paging/sort args, `scope_id`.

16. `view_request`
- Params: `request_id` (req), `part`, `search_pattern`, paging args.

17. `send_request`
- Params: `method` (req), `url` (req), `headers`, `body`, `timeout`.

18. `repeat_request`
- Params: `request_id` (req), `modifications`.

19. `scope_rules`
- Params: `action` (req), `allowlist`, `denylist`, `scope_id`, `scope_name`.

20. `list_sitemap`
- Params: `scope_id`, `parent_id`, `depth`, `page`.

21. `view_sitemap_entry`
- Params: `entry_id` (req).

### 8.7 python tools
22. `python_action`
- Purpose: persistent Python interpreter sessions.
- Params: `action` (req: new_session/execute/close/list_sessions), `code`, `timeout`, `session_id`.

### 8.8 reporting tools
23. `create_vulnerability_report`
- Purpose: persist validated finding with CVSS + dedupe check.
- Required fields include finding narrative + PoC + CVSS dimensions.
- Optional fields include endpoint/method/cve/code diff metadata.

### 8.9 terminal tools
24. `terminal_execute`
- Purpose: persistent tmux-backed shell execution.
- Params: `command` (req), `is_input`, `timeout`, `terminal_id`, `no_enter`.

### 8.10 thinking tools
25. `think`
- Purpose: structured reasoning note.
- Params: `thought` (req).

### 8.11 todo tools
26. `create_todo`
- Params: `title`, `description`, `priority`, `todos` (bulk).

27. `list_todos`
- Params: `status`, `priority`.

28. `update_todo`
- Params: single or bulk update model (`todo_id`/`updates` plus fields).

29. `mark_todo_done`
- Params: `todo_id` or `todo_ids`.

30. `mark_todo_pending`
- Params: `todo_id` or `todo_ids`.

31. `delete_todo`
- Params: `todo_id` or `todo_ids`.

### 8.12 web_search tools
32. `web_search`
- Purpose: Perplexity-backed real-time web search for security context.
- Params: `query` (req).
- Registered only when `PERPLEXITY_API_KEY` is configured.

---

## 9) Complete Skill Catalog (All Skill Files)

Skill loading code:
- `esprit/skills/__init__.py`
- `esprit/llm/llm.py`

Rules:
- user-visible skill validation excludes categories: `scan_modes`, `coordination`
- internal load still includes scan mode skill and root agent skill
- max explicit skills per subagent: 5

### 9.1 Coordination
1. `esprit/skills/coordination/root_agent.md`
- Root orchestration strategy for delegation, aggregation, and completion.

### 9.2 Scan modes
2. `esprit/skills/scan_modes/quick.md`
- Rapid, time-boxed, high-impact-first strategy.

3. `esprit/skills/scan_modes/standard.md`
- Balanced methodology with systematic coverage.

4. `esprit/skills/scan_modes/deep.md`
- Exhaustive depth and aggressive vulnerability chaining.

### 9.3 Vulnerabilities
5. `esprit/skills/vulnerabilities/authentication_jwt.md`
- JWT/OIDC forgery, confusion, claim abuse.

6. `esprit/skills/vulnerabilities/broken_function_level_authorization.md`
- Action-level authz bypass and privilege misuse.

7. `esprit/skills/vulnerabilities/business_logic.md`
- Workflow/state/invariant abuse.

8. `esprit/skills/vulnerabilities/csrf.md`
- CSRF token bypass and state-changing abuse.

9. `esprit/skills/vulnerabilities/idor.md`
- BOLA/IDOR object access violations.

10. `esprit/skills/vulnerabilities/information_disclosure.md`
- Data leakage, debug/errors, metadata exposure.

11. `esprit/skills/vulnerabilities/insecure_file_uploads.md`
- Upload validation bypass and execution pivots.

12. `esprit/skills/vulnerabilities/mass_assignment.md`
- Unauthorized field binding and privilege elevation.

13. `esprit/skills/vulnerabilities/open_redirect.md`
- Redirect parser/allowlist bypass and phishing/oauth pivots.

14. `esprit/skills/vulnerabilities/path_traversal_lfi_rfi.md`
- Traversal and file inclusion exploitation paths.

15. `esprit/skills/vulnerabilities/race_conditions.md`
- TOCTOU and concurrent state races.

16. `esprit/skills/vulnerabilities/rce.md`
- Command/deserialization/template code execution.

17. `esprit/skills/vulnerabilities/sql_injection.md`
- SQLi variants and bypass strategies.

18. `esprit/skills/vulnerabilities/ssrf.md`
- Internal reachability, metadata theft, protocol abuse.

19. `esprit/skills/vulnerabilities/subdomain_takeover.md`
- Dangling DNS and unclaimed resource takeover.

20. `esprit/skills/vulnerabilities/xss.md`
- Reflected/stored/DOM XSS with bypass methods.

21. `esprit/skills/vulnerabilities/xxe.md`
- External entity injection and parser abuse.

### 9.4 Frameworks
22. `esprit/skills/frameworks/django.md`
- Django-specific auth, ORM, CSRF/admin pitfalls.

23. `esprit/skills/frameworks/express.md`
- Express middleware/prototype pollution/NoSQL vectors.

24. `esprit/skills/frameworks/fastapi.md`
- FastAPI dependency, schema, ASGI attack patterns.

25. `esprit/skills/frameworks/nextjs.md`
- Next.js app-router/server-action/RSC concerns.

26. `esprit/skills/frameworks/rails.md`
- Rails mass assignment/deserialization/ActiveRecord vectors.

27. `esprit/skills/frameworks/spring_boot.md`
- Spring Boot actuator/SpEL/deserialization concerns.

### 9.5 Protocols
28. `esprit/skills/protocols/graphql.md`
- Resolver auth, batching, introspection and schema abuse.

29. `esprit/skills/protocols/grpc.md`
- Reflection, metadata authz, protobuf misuse.

30. `esprit/skills/protocols/oauth.md`
- Redirect URI, PKCE, code/token flow attacks.

31. `esprit/skills/protocols/websocket.md`
- CSWSH, message authz gaps, session binding flaws.

### 9.6 Cloud
32. `esprit/skills/cloud/aws.md`
- IAM/S3/metadata/lambda/cognito cloud attack paths.

33. `esprit/skills/cloud/azure.md`
- Entra ID/blob/keyvault/managed identity weaknesses.

34. `esprit/skills/cloud/gcp.md`
- Service accounts/storage/metadata/functions/fb vectors.

35. `esprit/skills/cloud/kubernetes.md`
- RBAC, SA tokens, pod escape, secrets/network policy gaps.

### 9.7 Reconnaissance
36. `esprit/skills/reconnaissance/osint.md`
- Public intel, historical and metadata-driven discovery.

37. `esprit/skills/reconnaissance/port_scanning.md`
- Port/service enumeration and evasion-oriented scanning.

38. `esprit/skills/reconnaissance/subdomain_enumeration.md`
- DNS/passive/active subdomain mapping patterns.

39. `esprit/skills/reconnaissance/technology_fingerprinting.md`
- Stack and control-surface identification.

40. `esprit/skills/reconnaissance/web_content_discovery.md`
- Path/endpoint/content discovery techniques.

### 9.8 Technologies
41. `esprit/skills/technologies/firebase_firestore.md`
- Firestore rules/auth/functions trust boundary issues.

42. `esprit/skills/technologies/supabase.md`
- RLS/PostgREST/edge functions/service-key risk patterns.

### 9.9 Skills meta doc
43. `esprit/skills/README.md`
- Skill architecture, categories, and contribution guidance.

---

## 10) Prompt and Instruction Hierarchy

Primary file:
- `esprit/agents/EspritAgent/system_prompt.jinja`

Layering order in practice:

```text
System prompt template (global behavior + orchestration rules)
  + tools section (XML fallback only)
  + specialized skill blocks
  + scan mode skill
  + root_agent skill (root only)
  + user task payload (targets/instructions)
  + message history and tool outputs
```

Important enforced behavior themes in prompt:
- aggressive parallel subagent decomposition
- finish discipline (`agent_finish` vs `finish_scan`)
- one tool call per agent message while active
- dynamic split between black-box, white-box, combined contexts

---

## 11) Observability, Artifacts, and Outputs

Primary file:
- `esprit/telemetry/tracer.py`

Artifacts per run (`esprit_runs/<run_name>`):
- `penetration_test_report.md`
- `vulnerabilities/vuln-XXXX.md` (one file per finding)
- `vulnerabilities.csv`

Telemetry includes:
- agent creation/status lifecycle
- tool execution lifecycle and result status
- token/cost aggregate stats
- streaming partial content handling

---

## 12) Key Operational Decision Matrix

```text
Need to test web UI behavior?
  -> browser_action (+ proxy capture underneath)

Need raw HTTP replay/fuzz from observed traffic?
  -> list_requests/view_request/repeat_request/send_request

Need custom scripting or sprays?
  -> python_action (persistent interpreter)

Need shell tools / scanners?
  -> terminal_execute (persistent tmux session)

Need code/file edits/search?
  -> str_replace_editor/list_files/search_files

Need orchestration/parallel workers?
  -> create_agent / wait_for_message / send_message_to_agent

Need structured findings persistence?
  -> create_vulnerability_report

Root finalization?
  -> finish_scan (only when no active agents)

Subagent completion?
  -> agent_finish
```

---

## 13) Short End-to-End Sequence (ASCII)

```text
User starts scan
  -> Root agent receives target map
  -> Root performs orientation/recon
  -> Root spawns specialized subagents in parallel
  -> Each subagent runs LLM->Tool->Result loops
  -> Subagents validate and report back via agent_finish
  -> Reporting agents call create_vulnerability_report
  -> Root waits for all active agents to complete
  -> Root calls finish_scan
  -> Tracer writes final report + vuln files
```

---

## 14) Reference File Index

Core runtime/orchestration:
- `esprit/interface/main.py`
- `esprit/interface/cli.py`
- `esprit/interface/utils.py`
- `esprit/agents/EspritAgent/esprit_agent.py`
- `esprit/agents/base_agent.py`
- `esprit/agents/state.py`
- `esprit/runtime/docker_runtime.py`
- `esprit/runtime/tool_server.py`
- `esprit/runtime/__init__.py`

LLM/prompt/tool-call:
- `esprit/llm/llm.py`
- `esprit/llm/utils.py`
- `esprit/llm/memory_compressor.py`
- `esprit/agents/EspritAgent/system_prompt.jinja`
- `esprit/providers/antigravity_format.py`

Tools and schemas:
- `esprit/tools/__init__.py`
- `esprit/tools/registry.py`
- `esprit/tools/executor.py`
- `esprit/tools/argument_parser.py`
- `esprit/tools/*/*_actions.py`
- `esprit/tools/*/*_schema.xml`

Skills/docs:
- `esprit/skills/README.md`
- `esprit/skills/**/*.md`
- `docs/advanced/skills.mdx`
- `docs/tools/*.mdx`
- `docs/usage/scan-modes.mdx`
- `docs/usage/instructions.mdx`
- `docs/advanced/configuration.mdx`
- `docs/antigravity.md`

