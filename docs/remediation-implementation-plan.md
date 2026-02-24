# Implementation Plan: Staged Vulnerability Remediation

## Context

When a user runs `esprit scan ./my-local-project` (white-box mode), the agent discovers
vulnerabilities but never patches them. The `str_replace_editor` tool is registered,
schema'd, and passed to the LLM — but agents never invoke it because:

1. The system prompt says "FIX" but never names the tool
2. No remediation skill exists (17 testing skills, 0 fixing skills)
3. The multi-agent workflow stops at Reporting — no Fixing Agent is ever spawned

**Reference**: OpenCode (opencode-ai) uses a **Plan → Build** mode separation with
permission-level enforcement, a 5-phase planning workflow, and per-agent tool access
control. We borrow the concept of **stages with tool gating** but adapt it to Esprit's
pentest-specific multi-agent architecture.

---

## Design: Two-Stage White-Box Workflow

### Current Flow (broken)
```
Discovery → Validation → Reporting → agent_finish (STOP)
                                      ↑ str_replace_editor available but never used
```

### Proposed Flow
```
┌─────────────────────────────────────────────────────────────────┐
│ STAGE 1: ASSESS                                                 │
│ (current behavior, unchanged)                                   │
│                                                                 │
│ Discovery Agent → Validation Agent → Reporting Agent            │
│ Tools: all scanning tools, browser, terminal, python, proxy     │
│ Goal: find and report all vulnerabilities                       │
│                                                                 │
│ Root agent calls finish_scan when all vulns are reported        │
│                                                                 │
├─────────────────────────────────────────────────────────────────┤
│ STAGE 2: REMEDIATE                                              │
│ (new — only in white-box mode)                                  │
│                                                                 │
│ Root agent reviews all reported vulns                           │
│     ↓                                                           │
│ For each vuln with fixable code:                                │
│     Spawns "Fixing Agent" with skills: [remediation, <vuln>]    │
│         ↓                                                       │
│     Fixing Agent:                                               │
│       1. Reads vulnerable file (str_replace_editor view)        │
│       2. Applies fix (str_replace_editor str_replace)           │
│       3. Re-runs exploit to verify fix                          │
│       4. Calls agent_finish with diff summary                   │
│                                                                 │
│ Root agent calls finish_scan (final) with remediation summary   │
└─────────────────────────────────────────────────────────────────┘
```

### Why Two Stages Instead of Inline Fixing

OpenCode's approach: Plan agent (read-only) → Build agent (full access). The key insight
is **separation of concerns with permission enforcement**, not just prompt instructions.

We apply the same principle:
- **Stage 1 (Assess)**: Agents focus on finding vulns. They can READ code but are NOT
  instructed to modify it. This prevents premature patching before a vuln is validated and
  reported (which the current prompt already enforces at line 147-149).
- **Stage 2 (Remediate)**: Fixing Agents are explicitly spawned with the `remediation`
  skill and instructions to use `str_replace_editor`. They focus only on patching.

This avoids the problem of a single agent trying to find, validate, report, AND fix in
one pass — which leads to confused behavior and dropped steps.

---

## Implementation Steps

### Step 1: Create the Remediation Skill

**File**: `esprit/skills/vulnerabilities/remediation.md` (new)

This skill teaches Fixing Agents HOW to use `str_replace_editor` for patching. It
includes:
- Exact tool name and parameter examples (`command="str_replace"`, `old_str=...`, `new_str=...`)
- Fix patterns for each vulnerability class (SQLi → parameterized queries, XSS → output
  encoding, IDOR → authz checks, SSRF → URL allowlisting, RCE → safe alternatives)
- The view → edit → verify workflow
- Instructions to use `search_files` to find all instances of a vulnerable pattern
- Instructions to use `undo_edit` if a fix breaks something

**Why this matters**: OpenCode's edit tool description is embedded directly in the tool's
schema file and explicitly referenced in the system prompt. Esprit's `str_replace_editor`
has a schema but is never mentioned in the prompt. The skill bridges this gap — when a
Fixing Agent loads the `remediation` skill, it gets concrete instructions injected into
its context.

**Skill loading mechanism** (already works, no code change needed):
- `esprit/skills/__init__.py:17-31` auto-discovers `.md` files in category directories
- `get_available_skills()` returns `{"vulnerabilities": [..., "remediation", ...]}`
- When `create_agent(skills="remediation,sql_injection")` is called, the skill content is
  loaded and injected into the agent's system prompt via Jinja template

---

### Step 2: Add Remediation Instructions to System Prompt

**File**: `esprit/agents/EspritAgent/system_prompt.jinja`

**Change A**: Add a `VULNERABILITY REMEDIATION` section after the white-box instructions
(after line 101). This section:
- Names `str_replace_editor` explicitly as the tool for patching
- Names `list_files` and `search_files` as supporting tools
- Provides the remediation workflow (view → edit → verify)
- Lists common fix patterns by vulnerability type
- States: "NEVER leave a discovered vulnerability unpatched in white-box mode"

**Change B**: Update the `CRITICAL RULES` section (around line 272) to add:
```
- **WHITE-BOX FIXING IS MANDATORY** — After ALL vulnerabilities are reported in
  white-box mode, the root agent MUST enter a remediation stage: spawn Fixing Agents
  for each reported vulnerability that has fixable source code. Each Fixing Agent uses
  `str_replace_editor` to patch the code and verifies the fix by re-running the exploit.
```

**Change C**: Update the white-box workflow diagram (lines 261-270) to show the
mandatory Fixing Agent step:
```
WHITE-BOX WORKFLOW (source code provided):

Authentication Code Agent finds weak password validation
    ↓
Spawns "Auth Validation Agent" (proves it's exploitable)
    ↓
If valid → Spawns "Auth Reporting Agent" (creates vulnerability report)
    ↓
MANDATORY → Spawns "Auth Fixing Agent" with skills: [remediation, authentication_jwt]
  - Uses str_replace_editor to patch the vulnerable code
  - Re-runs the original exploit to confirm it no longer works
  - Calls agent_finish with the diff summary
```

**Change D**: Update the `finish_scan` constraint to enforce remediation in white-box
mode. Currently `finish_scan` just checks that no subagents are running. Add a check:
if white-box mode AND there are reported vulnerabilities with no corresponding fixing
agent completions, warn the root agent to spawn fixing agents first.

This is a **soft enforcement** (warning, not blocking) to avoid breaking the flow if
fixing is genuinely impossible (e.g., minified third-party code). The system prompt
instruction is the primary enforcement mechanism.

---

### Step 3: Update `finish_scan` for White-Box Awareness

**File**: `esprit/tools/finish/finish_actions.py`

Currently `finish_scan` (line 86-150) validates that all subagents have finished and
all fields are non-empty. Add a white-box check:

```python
# After line 100 (active_agents_error check):
if _is_whitebox_scan(agent_state):
    remediation_warning = _check_remediation_completeness(agent_state)
    if remediation_warning:
        return remediation_warning  # Soft block: tells agent to fix first
```

**`_is_whitebox_scan`**: Check if `agent_state` has `local_sources` (inherited from
`BaseAgent.local_sources` at `base_agent.py:65`). This requires threading the
white-box flag through `AgentState` — currently `local_sources` is on the agent
instance, not on the state object.

**`_check_remediation_completeness`**: Look at the agent graph for completed "Fixing
Agent" nodes. If there are vulnerability reports but no corresponding fixing agents,
return a soft warning message:
```python
{
    "success": False,
    "error": "remediation_incomplete",
    "message": "White-box scan has N reported vulnerabilities without fixes. "
               "Spawn Fixing Agents with skills=[remediation, <vuln_type>] to "
               "patch the code before finishing the scan."
}
```

This mirrors OpenCode's approach where the plan agent literally cannot call edit
tools (permission: deny). Here, we don't deny `finish_scan` permanently — we just
bounce the root agent back to spawn fixing agents.

---

### Step 4: Thread White-Box Flag Through Agent State

**File**: `esprit/agents/state.py`

Currently `AgentState` (line 11-26) has no concept of scan type. Add:

```python
class AgentState:
    def __init__(self, ..., is_whitebox: bool = False):
        self.is_whitebox = is_whitebox
```

**File**: `esprit/agents/base_agent.py`

At line 79-82 where `AgentState` is created for the root agent, pass `is_whitebox`:

```python
self.state = AgentState(
    agent_name="Root Agent",
    max_iterations=self.max_iterations,
    is_whitebox=bool(self.local_sources),  # NEW
)
```

**File**: `esprit/tools/agents_graph/agents_graph_actions.py`

At line 392 where `AgentState` is created for subagents, inherit from parent:

```python
parent_agent = _agent_instances.get(parent_id)
is_whitebox = getattr(parent_agent, "state", None)
is_whitebox = is_whitebox.is_whitebox if is_whitebox else False

state = AgentState(
    task=task,
    agent_name=name,
    parent_id=parent_id,
    max_iterations=300,
    is_whitebox=is_whitebox,  # NEW: inherit from parent
)
```

This is lightweight — just a boolean flag propagated through the agent tree. It lets
`finish_scan` know it should check for remediation completeness without inspecting
the CLI args or environment.

---

### Step 5: Create the Remediation Skill File

**File**: `esprit/skills/vulnerabilities/remediation.md` (new)

Content structure:

```markdown
---
name: remediation
description: Code vulnerability remediation using str_replace_editor
---

# Vulnerability Remediation

## Your Role
You are a Fixing Agent. Your job is to patch a specific vulnerability that was
already discovered, validated, and reported by other agents. You receive the
vulnerability report in your inherited context.

## Remediation Workflow
1. Read the vulnerability report from your inherited context
2. Use `list_files` to locate the affected source files
3. Use `str_replace_editor(command="view", path=...)` to read the vulnerable code
4. Use `search_files` to find all instances of the vulnerable pattern
5. Apply the fix with `str_replace_editor(command="str_replace", ...)`
6. Verify: re-run the original exploit — it should fail
7. If the fix breaks functionality, use `str_replace_editor(command="undo_edit", ...)`
8. Call `agent_finish` with a summary including the diff

## Fix Patterns
[SQL Injection, XSS, IDOR, SSRF, RCE, Path Traversal — each with
vulnerable/fixed code examples and exact str_replace_editor calls]

## Tool Reference
[Exact parameter documentation for str_replace_editor, list_files, search_files]

## Rules
- Fix the ROOT CAUSE, not the symptom
- One vulnerability per Fixing Agent
- Always verify the fix by re-running the exploit
- Never modify files outside the project directory
- Preserve the project's existing code style
```

---

### Step 6: Update Root Agent Coordination Skill

**File**: `esprit/skills/coordination/root_agent.md`

The root agent skill controls how the root agent orchestrates subagents. Add
remediation stage instructions:

After the existing workflow instructions, add a section:

```markdown
## Remediation Stage (White-Box Only)

After all vulnerabilities have been reported (Stage 1 complete), enter the
remediation stage:

1. Review all vulnerability reports from the assessment stage
2. For each vulnerability that has fixable source code:
   - Spawn a Fixing Agent: `create_agent(name="<Vuln> Fixing Agent",
     task="Fix <description> in <file>", skills="remediation,<vuln_type>")`
   - The Fixing Agent receives the vulnerability report via inherited context
   - Wait for the Fixing Agent to complete
3. After all Fixing Agents complete, call `finish_scan` with a remediation
   summary in the `recommendations` field

Skip remediation for:
- Vulnerabilities in third-party/vendor code you don't control
- Configuration-only issues (server headers, TLS settings)
- Vulnerabilities that require architectural changes beyond single-file edits
```

---

## Files Changed Summary

| File | Change Type | Priority | Description |
|------|------------|----------|-------------|
| `esprit/skills/vulnerabilities/remediation.md` | **CREATE** | P0 | Remediation skill with fix patterns and tool examples |
| `esprit/agents/EspritAgent/system_prompt.jinja` | EDIT | P0 | Add VULNERABILITY REMEDIATION section, update workflow diagram, add mandatory fixing rule |
| `esprit/skills/coordination/root_agent.md` | EDIT | P0 | Add remediation stage instructions for root agent |
| `esprit/agents/state.py` | EDIT | P1 | Add `is_whitebox` flag to `AgentState` |
| `esprit/agents/base_agent.py` | EDIT | P1 | Pass `is_whitebox` to `AgentState` constructor |
| `esprit/tools/agents_graph/agents_graph_actions.py` | EDIT | P1 | Inherit `is_whitebox` from parent agent |
| `esprit/tools/finish/finish_actions.py` | EDIT | P1 | Add remediation completeness check in white-box mode |

**No changes needed to:**
- `esprit/tools/file_edit/*` — tool is already functional
- `esprit/tools/registry.py` — tool is already registered with JSON schema
- `esprit/llm/llm.py` — tools are already passed to the LLM API
- `esprit/tools/executor.py` — tool execution pipeline is already complete

---

## How It Works End-to-End

### User runs: `esprit scan ./my-flask-app`

**Stage 1 — Assess** (existing behavior, unchanged):
```
Root Agent (skill: root_agent)
├── SQLi Discovery Agent (skill: sql_injection)
│   └── finds SQL injection at app.py:42
│       └── SQLi Validation Agent (skill: sql_injection)
│           └── proves it with PoC (sqlmap)
│               └── SQLi Reporting Agent (skill: sql_injection)
│                   └── calls create_vulnerability_report()
├── XSS Discovery Agent (skill: xss)
│   └── finds XSS at templates/profile.html:18
│       └── XSS Validation Agent (skill: xss)
│           └── proves it with alert(1)
│               └── XSS Reporting Agent (skill: xss)
│                   └── calls create_vulnerability_report()
```

Root agent tries to call `finish_scan` → gets bounced:
```
"White-box scan has 2 reported vulnerabilities without fixes.
 Spawn Fixing Agents to patch the code before finishing."
```

**Stage 2 — Remediate** (new):
```
Root Agent
├── SQLi Fixing Agent (skills: remediation, sql_injection)
│   ├── str_replace_editor(command="view", path="app.py")
│   ├── str_replace_editor(command="str_replace",
│   │     old_str='query = f"SELECT * FROM users WHERE id = {user_id}"',
│   │     new_str='query = "SELECT * FROM users WHERE id = %s"')
│   ├── re-runs sqlmap exploit → fails (FIXED)
│   └── agent_finish(result_summary="Patched SQLi: parameterized query")
│
├── XSS Fixing Agent (skills: remediation, xss)
│   ├── str_replace_editor(command="view", path="templates/profile.html")
│   ├── str_replace_editor(command="str_replace",
│   │     old_str='{{ user.bio }}',
│   │     new_str='{{ user.bio | e }}')
│   ├── re-runs XSS exploit → fails (FIXED)
│   └── agent_finish(result_summary="Patched XSS: added Jinja2 escape filter")
```

Root agent calls `finish_scan` with remediation summary → scan complete.

---

## Comparison with OpenCode's Approach

| Aspect | OpenCode | Esprit (Proposed) |
|--------|----------|-------------------|
| **Mode separation** | Plan Agent (read-only) vs Build Agent (full access) | Stage 1 Assess (find vulns) vs Stage 2 Remediate (fix code) |
| **Enforcement** | Permission-level: plan agent has `edit: "deny"` | Prompt-level + soft gate: `finish_scan` bounces if no fixes |
| **Tool gating** | Per-agent permission config (allow/ask/deny) | Per-stage via system prompt instructions (which tools to use when) |
| **Transition** | `plan_exit` tool switches to build agent | `finish_scan` bounce triggers root agent to spawn Fixing Agents |
| **Scope** | General-purpose coding (any file edit) | Pentest-specific (only fix reported vulns) |
| **Skills** | No skill system | Remediation skill teaches fix patterns per vuln type |

### What we borrow from OpenCode:
1. **Stage concept** — clear separation between analysis and modification phases
2. **Soft gating** — root agent can't finish until remediation is done (like plan exit requires plan file)
3. **Tool naming in instructions** — OpenCode's prompts explicitly name tools; we do the same for `str_replace_editor`

### What we don't borrow:
1. **Permission-level enforcement** — OpenCode denies tools at runtime. We use prompt instruction + `finish_scan` gate instead. Adding a full permission system is a larger refactor and not needed for this focused use case.
2. **Fuzzy edit matching** — OpenCode's 9-stage replacer cascade is a nice-to-have but not blocking. The LLM is good at providing exact strings for `str_replace`. Can be added later.
3. **Model-specific tool routing** — GPT gets `apply_patch`, Claude gets `edit`. Not needed since Esprit's `str_replace_editor` works across all models.

---

## Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Fixing Agent applies wrong patch | Agent must re-run exploit to verify. `undo_edit` available as rollback. |
| Fix breaks application functionality | Prompt instructs agent to run tests if available. If no tests, agent verifies manually. |
| Agent cap hit (MAX_AGENTS=10) | Fixing Agents are spawned after assessment agents finish and are cleaned up. Worst case: raise cap for white-box mode. |
| Fixing takes too long | Each Fixing Agent has `max_iterations=300`. If it can't fix within budget, it calls `agent_finish(success=False)`. |
| Non-fixable vulns (config issues, 3rd party) | Root agent skill instructs to skip these. `finish_scan` gate accepts if all fixable vulns have fixing agents. |
| `finish_scan` infinite bounce loop | Add max bounce count (e.g., 2). After second bounce, allow `finish_scan` with warning in report. |

---

## Testing Plan

1. **Unit test**: `remediation.md` skill loads correctly via `get_available_skills()`
2. **Unit test**: `AgentState.is_whitebox` flag propagates from root to child agents
3. **Unit test**: `finish_scan` returns remediation warning when white-box + no fixes
4. **Unit test**: `finish_scan` passes when white-box + all vulns have fixing agents
5. **Integration test**: White-box scan on a deliberately vulnerable Flask app
   - Verify: vulns are discovered, reported, AND patched
   - Verify: `str_replace_editor` is called with correct parameters
   - Verify: exploit re-runs fail after fix
6. **Regression test**: Black-box scan behavior unchanged (no remediation stage)
