# Sprint 2 Ship Report — add-mcp-client

**Date**: 2026-05-16
**Branch**: `main`
**OpenSpec change**: `openspec/changes/add-mcp-client/`
**Strategic context**: Sprint 2 of the post-pivot roadmap — second sprint after `fix-tool-approval-and-permission-tiers` (Sprint 1) shipped 2026-05-15.

---

## TL;DR

Connects PyClaw to the Anthropic Model Context Protocol ecosystem. After
five lines of `pyclaw.json` config, the agent gains tools from any MCP-
compliant server (`@modelcontextprotocol/server-filesystem`,
`@modelcontextprotocol/server-github`, etc.) — with the same per-call
permission tier gate that Sprint 1 introduced for builtins, plus per-server
operator overrides (de-escalation only).

* **5 commits** (`70d2c47` → `c5d587d`) over one engineering session.
* **+109 tests, 0 regression** (final: 2173 passed, 32 skipped — baseline 2064).
* **Survives 4 review rounds** (v1→v4 of the OpenSpec artifacts) — v4 GATE
  passed with 0 red findings; §9 Oracle implementation review verdict
  READY-TO-CONTINUE.
* **Real-MCP E2E proven**: gated tests connect to a real `npx -y
  @modelcontextprotocol/server-filesystem` and dispatch through the full
  registry → dispatcher → adapter → SDK chain.

The implementation is **end-to-end functional**: setting `mcp.enabled = true`
in `pyclaw.json` causes PyClaw to spawn MCP server subprocesses in a
non-blocking background supervisor, register their tools live as servers
come online, route them through the same `ToolApprovalHook` Sprint 1
shipped, and expose `/mcp list / restart / logs` slash commands for ops.

---

## Phase-by-phase log

| # | Commit | Description | Tests | Risk |
|---|--------|-------------|-------|------|
| 1 | `70d2c47` | §0-§3: TaskCategory + Settings + ToolRegistry critical-section refactor + dispatcher MCPServerDeadError early-catch | 2106 (+42) | **HIGH** (Sprint 1 hot path) |
| 2 | `e086c8c` | §4-§5: MCPToolAdapter (dual-name pattern) + MCPClientManager (non-blocking supervisor + restart_server) | 2145 (+39) | **HIGH** (concurrent state machine) |
| 3 | `5eb405d` | §6+§8: lifespan integration + factory hook + runner per-call tier eval (de-escalation rank algorithm) + audit log `tier_source` field | 2155 (+10) | **HIGH** (Sprint 1 runner.py modification) |
| 4 | `92ef4ba` | §7: `/mcp` slash command (list / restart / logs) wired through Web + Feishu adapters | 2168 (+13) | medium |
| 5 | `c5d587d` | §10-§11: bilingual `docs/{en,zh}/mcp.md` + permissions/configuration cross-links + READMEs + example config | 2168 (no code) | low |
| (post) | (this commit) | §12-§14: failure-mode integration test + gated real-MCP E2E + ship report | 2173 (+5) | low |

The three high-risk commits each ran **full regression** before commit and
landed with **zero existing-test regression** thanks to the spec's
backward-compat amendment ("hook contract widens from single-tier batch to
approval-tier subset of mixed-tier batch") — for builtin-only iterations
the new per-call eval produces an identical batch as the old per-iteration
eval (no MCP tools means no `forced_tier`, all calls get the same
`per_turn_tier`).

---

## Files changed

### New (15 files, 868 LOC implementation + 1675 LOC tests)

```
src/pyclaw/integrations/__init__.py            7 LOC  (namespace boundary)
src/pyclaw/integrations/mcp/__init__.py       44 LOC  (public API surface)
src/pyclaw/integrations/mcp/errors.py         41 LOC  (MCPServerDeadError — defined here for topology)
src/pyclaw/integrations/mcp/settings.py      156 LOC  (Pydantic McpSettings + McpServerConfig + env substitution)
src/pyclaw/integrations/mcp/adapter.py       182 LOC  (MCPToolAdapter — dual-name pattern, exception map)
src/pyclaw/integrations/mcp/client_manager.py 438 LOC (MCPClientManager — supervisor / locks / death handler)
src/pyclaw/core/commands/mcp.py               ~110 LOC (/mcp list/restart/logs handler)
docs/en/mcp.md                                ~250 LOC (bilingual MCP guide — EN)
docs/zh/mcp.md                                ~250 LOC (bilingual MCP guide — ZH)
tests/unit/integrations/mcp/{__init__,test_settings,test_env_substitution,test_adapter,test_client_manager}.py
tests/unit/core/agent/test_runner_per_call_tier.py
tests/unit/core/agent/tools/test_registry_mcp_naming.py
tests/unit/core/commands/test_mcp_command.py
tests/integration/test_mcp_failure_modes.py
tests/integration/test_mcp_e2e.py            (gated by PYCLAW_TEST_MCP=1)
reports/add-mcp-client-ship-2026-05-16.md    (this file)
```

### Modified (Sprint 1 surface touched)

```
pyproject.toml                              + mcp>=1.12.4,<2.0
configs/pyclaw.example.json                 + mcp block (3 illustrative servers, disabled)
src/pyclaw/infra/task_manager.py            + "mcp" added to TaskCategory Literal
src/pyclaw/infra/settings.py                + Settings.mcp: McpSettings
src/pyclaw/core/agent/tools/registry.py     register validation + bidirectional get() + unregister() + _to_openai_function rewrite + _dispatch_single MCPServerDeadError early-catch
src/pyclaw/core/agent/runner.py             AgentRunnerDeps.mcp_death_handler + per-call tier evaluation (replaces per-iteration block at L666-719) + canonical name to hook + _emit_runner_audit accepts tier_source/forced_server
src/pyclaw/core/agent/factory.py            create_agent_runner_deps accepts external_tool_registrar + mcp_death_handler
src/pyclaw/app.py                           lifespan constructs MCPClientManager + start_background + attach_and_register wiring + /health mcp field + shutdown ordering
src/pyclaw/core/commands/{builtin,context}.py    /mcp registration + CommandContext.mcp_manager
src/pyclaw/channels/{web,feishu}/command_adapter.py    mcp_manager threaded through to ctx
src/pyclaw/channels/web/chat.py             mcp_manager from app.state.mcp_manager
src/pyclaw/channels/feishu/{handler,webhook}.py    mcp_manager threaded through FeishuChannelContext
README.md / README_CN.md                    status table row, docs cross-link, roadmap entry, test count badge
docs/{en,zh}/{permissions,configuration,README}.md   cross-links to mcp.md
tests/unit/core/commands/test_{builtin,steering_registration}.py    +1 expected command (/mcp)
```

---

## Architectural highlights

### 1. Dual-name adapter pattern (closes review v3 J1)

```
MCPToolAdapter.name      = f"{config_key}:{remote_tool_name}"   # canonical
MCPToolAdapter._sdk_key  = remote_tool.name                      # what SDK stores
```

Without this split, a naive design would hit a `KeyError` at runtime: the
SDK keys `group.tools` by `_component_name(name, server_info)`, which —
with no hook — returns the bare `tool.name`, NOT the canonical
`{server}:{tool}` form. The adapter calls `group.call_tool(self._sdk_key, args)`,
not `self.name`. The registry stores by `self.name` so the LLM-facing
schema and the runner's per-call tier evaluation can address tools by
canonical name.

### 2. Centralized `:` ↔ `__` rewrite in `ToolRegistry.get()`

LLM provider regex (`^[a-zA-Z0-9_-]+$`) rejects `:`, so `_to_openai_function`
rewrites `:` → `__` outbound. Inbound, `ToolRegistry.get(name)` first tries
literal lookup; on miss with `__` in the name, replaces the FIRST `__` with
`:` and tries again. This means **every caller of `registry.get()` gets the
rewrite for free** — including the runner's read-only-tier branch
(`runner.py:672`), the approval-hook batch construction (`runner.py:709`),
and the post-execution truncation lookup (`runner.py:755`). No duplicated
rewrite logic across call sites.

### 3. `MCPServerDeadError` caught INSIDE `_dispatch_single` (closes v2 C1+H)

The dispatcher uses `asyncio.gather` (parallel) and a bare `for await` loop
(sequential), neither with `return_exceptions=True`. If `MCPServerDeadError`
propagated out, `gather` would cancel sibling tasks and the post-dispatch
`assert r is not None` would crash. The dispatcher therefore catches it
internally, schedules `_handle_server_death` non-blockingly via
`task_manager.spawn` (reading `task_manager` + `mcp_death_handler` from
`ToolContext.extras`), and returns a normal error `ToolResult`. Sibling
parallel calls survive; sequential calls after the dead one still execute.

### 4. Non-blocking startup pattern (closes v3 G1 PEF refactor)

`MCPClientManager.start_background()` is **synchronous** — it spawns the
supervisor task via `task_manager.spawn` and returns immediately. The
supervisor runs `asyncio.gather(*[_connect_one for s in enabled_servers],
return_exceptions=True)` and sets `manager.ready: asyncio.Event` in a
`try/finally` block (so `ready.wait()` callers never hang on supervisor
crash). PyClaw's FastAPI lifespan does NOT block on per-server connectivity;
`/health` returns 200 OK immediately with an advisory `mcp` field. K8s
readiness probes are unaffected by MCP state — PyClaw stays alive even if
every MCP server fails. Closes 6 deployment scenarios documented in
`design.md` D7 (rolling upgrade, slow-server-blocks-everything, CI waits,
hot reload, diagnostic observability, framework-consistency).

### 5. `forced_tier` is de-escalation only (closes v3 B4 — security)

Operator config CANNOT escalate user permissions. Restrictiveness rank:
`{read-only: 2, approval: 1, yolo: 0}`. `forced_tier` only takes effect
when `RANK[forced] > RANK[per_turn]` (strictly more restrictive). So
`forced_tier="yolo"` is effectively a no-op (rank 0 ≤ any user choice),
and `forced_tier="approval"` is inert when the user already chose
`read-only`. This prevents a server-config bug from silently weakening
the user's permission gate.

### 6. Per-server `asyncio.Lock` for state mutations

Both `_connect_one` (initial supervisor connection AND user-initiated
reconnect) and `restart_server` acquire the same per-server lock before
mutating `_adapters[name]` / `_servers[name]` / registering or
unregistering adapters. `_handle_server_death` also acquires the lock and
is idempotent (early-returns if already `failed`). This serializes:

* Concurrent `/mcp restart` of the same server
* `/mcp restart` racing with the initial supervisor connection
* Mid-call death detection racing with `/mcp restart`

### 7. Bidirectional name validation at registration

* Builtin tool names: MUST NOT contain `:` (reserved for MCP namespace)
  AND MUST NOT contain `__` (would collide with the rewrite output).
* MCP server config keys: MUST NOT contain `:` or `__` (Pydantic
  validation at config load time).
* MCP-imported tool names: MUST contain exactly one `:` (canonical
  `{server}:{tool}`); server prefix MUST NOT contain `__`.
* Remote tool names with `:` are rejected at adapter construction with a
  per-tool warning; the rest of the server still loads.

These constraints structurally prevent name collisions across the full
canonical-form ↔ LLM-form ↔ SDK-key triangle.

---

## Test breakdown (109 net new tests)

| File | Tests | Focus |
|------|-------|-------|
| `test_settings.py` | 12 | Pydantic validation, server-key constraints, `MCPServerDeadError` constructor |
| `test_env_substitution.py` | 9 | `{env:VAR}` substitution: resolved / missing / literal / partial / case sensitivity / leading underscore / whitespace |
| `test_adapter.py` | 21 | `tool_class` derivation precedence, dual-name pattern, content-block conversion (5 SDK types), exception map (timeout / dead / non-dead OSError / connection-loss McpError / non-connection McpError) |
| `test_client_manager.py` | 18 | Server status init, `start_background` disabled fast-path, supervisor crash → ready set, env var missing, `_handle_server_death` idempotency, `attach_and_register` idempotency, restart of unknown / disabled, log redaction (resolved + literal env values), shutdown |
| `test_registry_mcp_naming.py` | 21 | Bidirectional `get()`, name validation (builtin / MCP / unknown), `unregister` semantics, `_to_openai_function` rewrite, `_dispatch_single` `MCPServerDeadError` paths (normal / no extras / spawn failure / sibling preservation in gather) |
| `test_runner_per_call_tier.py` | 10 | De-escalation rank algorithm: forced_tier="approval" gates over yolo per-turn; forced_tier="yolo" cannot escalate over per-turn approval; read-only per-turn cannot be relaxed; mixed-batch yolo+forced; canonical name resolution via registry |
| `test_mcp_command.py` | 13 | `/mcp` disabled path, usage / unknown subcommand, list (empty / with servers / pending dash / never), restart (missing name / success / failure), logs (missing / unknown / empty / with content) |
| `test_mcp_failure_modes.py` (integration) | 5 | Dead server doesn't cancel sibling parallel calls, multiple dead-call death-handler idempotency, slow startup non-blocking, restart failure removes old adapters, concurrent death collapses |
| `test_mcp_e2e.py` (gated, `PYCLAW_TEST_MCP=1`) | 2 | Real `npx @modelcontextprotocol/server-filesystem` lifecycle + dispatch through full registry chain |

---

## Review rounds — methodology recap

The OpenSpec artifacts went through **4 adversarial review rounds** before
implementation began, each with 4 parallel slot agents (Oracle architecture
+ ground-truth grep + cross-consistency + adversarial counter-example):

| Round | Red findings | Notes |
|-------|--------------|-------|
| v1 | 5 🔴 | Initial draft — found wiring gaps + invariant ambiguity |
| v2 | 4 🔴 (regression + new) | First refactor introduced new bugs (forced_tier escalation, MCPServerDeadError gather cascade, lifecycle contradiction, topology bug) |
| v3 | 3 🔴 | After fixes; v3 review found per-server-tool-attribution gap, idempotency claim falsity, lifespan blocking PEF |
| v4 | 0 🔴 | All v3 issues closed; GATE passed. Some 🟡 fixed inline. |

This ship report corresponds to the **v4-passing** artifact set. The
methodology document (`DailyWork/reviews/2026-05-12-spec-review-methodology-lessons.md`)
gained a new blind-spot (#12: Pre-Existing Findings Triage) during the v3
round when the lifespan-blocking issue revealed the need to formalize
how PEFs get scoped (current-change blocker / friction / cleanup /
framework-pattern). User authorized inline refactor for v3.G1 (the lifespan
PEF) → non-blocking startup pattern landed in this Sprint.

---

## Deferred / out-of-scope (recorded for Sprint 2.1)

The following items are **explicitly deferred** and tracked as Sprint 2.1
follow-ups (will be entered into `KNOWN-ISSUES.md` under the `MCP-F*`
prefix):

* **MCP-F1**: SSE / streamable-http transports (Sprint 2 ships stdio only).
* **MCP-F2**: OAuth flow for remote MCP servers (Sprint 2 uses static
  `{env:VAR}` only).
* **MCP-F3**: Sampling — server reverse-calls our LLM (advanced MCP
  capability, not in Sprint 2).
* **MCP-F4**: `Resources` (read-only data) and `Prompts` (server-supplied
  prompt templates) — Sprint 2 only consumes `tools`. EmbeddedResource
  blocks in tool results render as `[resource: <uri>]` text fallback.
* **MCP-F5**: Auto-restart with backoff on server crash (Sprint 2 requires
  manual `/mcp restart`).
* **MCP-F6**: ClawHub MCP server marketplace integration (Sprint 2 servers
  are operator-configured only).
* **MCP-F7**: Cross-host failure-mode pattern survey (Continue.dev / Cline /
  Cursor / opencode) — librarian background task timed out at 30 min on
  ship day; the §12.2 failure-mode integration tests already cover the
  spec's stated invariants.
* **MCP-F8**: Per-tool schema size cap (huge `inputSchema` documents could
  blow the prompt budget; Sprint 2 has only the >100 total-tool warning).
* **MCP-F9**: Framework-level non-blocking startup adoption (extend the
  pattern Sprint 2 introduced for MCP to memory_store / redis init / etc.).
* **MCP-F10**: Web UI affordance — show MCP tools with a distinguishing
  badge (today they show with the same chrome as builtins). The spec
  guarantees frontend works unchanged but visual distinction would help
  users understand tool provenance.

---

## Manual smoke checklist (post-merge, on a deployment with MCP enabled)

These were **deferred from §13.2-13.4** of the implementation tasks because
they require a running PyClaw + a real MCP server. The automated equivalent
landed in `tests/integration/test_mcp_e2e.py`, but full UX verification
needs a live deployment.

For the operator who first deploys with `mcp.enabled=true`:

1. **`/mcp list` returns immediately** (no blocking on per-server timeouts).
2. **`/health` body includes `mcp: {n_pending, n_connected, ...}`** within
   1 second of PyClaw startup, regardless of whether servers have connected.
3. **Web channel chat triggers MCP tool** → approval modal renders with
   **canonical name** (e.g., `github:search_issues`, NOT `github__search_issues`)
   AND tier toggle still works.
4. **Feishu channel chat triggers MCP tool** → CardKit interactive card
   renders with canonical name AND originator-only authorization is
   enforced (Sprint 1 behavior preserved).
5. **`/mcp restart <name>` after killing a server externally** → recovers
   without a full PyClaw restart.
6. **Privilege-escalation regression**: user picks `approval` per-turn, server
   has `forced_tier="yolo"` — call STILL goes through approval modal (the
   v3.B4 security fix).

---

## Verifying with a real MCP server right now

```bash
# 1. Add to pyclaw.json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "fs": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/projects"]
      }
    }
  }
}

# 2. Start PyClaw
./scripts/start.sh

# 3. Watch /health
curl http://localhost:8000/health
# {"status": "ok", ..., "mcp": {"ready": false, "n_pending": 1, ...}}

# … 5 seconds later …
curl http://localhost:8000/health
# {"status": "ok", ..., "mcp": {"ready": true, "n_connected": 1, "total_tools": 11, ...}}

# 4. Confirm tools registered
curl -X POST http://localhost:8000/api/.../chat ...
# Send: "List the files in this folder"
# Agent calls fs:list_directory → gets file list
```

Or run the gated automated E2E to confirm without manual smoke:

```bash
PYCLAW_TEST_MCP=1 .venv/bin/pytest tests/integration/test_mcp_e2e.py -v
# 2 passed in ~7s
```

---

## Acceptance criteria — all met

* [x] `pyproject.toml` + `Settings.mcp` + `McpServerConfig` validators
* [x] `MCPClientManager` non-blocking startup + per-server lock + supervisor
* [x] `MCPToolAdapter` dual-name pattern + exception map
* [x] `ToolRegistry` bidirectional `get()` + name validation + `unregister`
* [x] `_dispatch_single` `MCPServerDeadError` early-catch (never propagates)
* [x] Runner per-call tier eval + de-escalation rank + canonical name to hook
* [x] Audit log `tier_source` + `forced_server` fields (backward-compat)
* [x] `/mcp list / restart / logs` slash command (Web + Feishu)
* [x] Lifespan integration + `/health` advisory `mcp` field
* [x] Bilingual `docs/{en,zh}/mcp.md` + cross-links + READMEs
* [x] 109 net new tests, 0 regression (2173 passed, 32 skipped)
* [x] Real-MCP E2E gated tests pass (`PYCLAW_TEST_MCP=1`)
* [x] §9 Oracle implementation review verdict: READY-TO-CONTINUE
* [x] All v4 review-round findings closed
* [x] Sprint 1 backward compatibility verified (builtin-only iterations
      produce identical batches as before)

---

## Next steps

1. **Manual real-machine smoke** by user (the §13.2-13.4 deferred items
   above) on a deployment with `mcp.enabled=true`.
2. **Open MCP-F1 through MCP-F10 follow-ups** in `KNOWN-ISSUES.md`.
3. **Archive the OpenSpec change** (`openspec archive add-mcp-client`).
4. **Push to origin/main**.

Sprint 3 (per the post-pivot roadmap) is `add-knowledge-base` (user/agent
visible KB, parallel to the 4-layer memory). Both Sprint 2 and Sprint 3
depend on the Sprint 1 permission tier system, which is now demonstrably
extensible via the duck-typed `Tool` Protocol + per-call tier evaluation
pattern Sprint 2 added.
