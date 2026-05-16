# Sprint 2.0.1 Hotfix Smoke Report — Tool-Approval Phantom Modal Fix

**Date**: 2026-05-16 (evening)
**Branch**: `main`
**Commits**: `5dae41d`, `8d167f2`, `e1bfbfb` (3 commits, pushed `ab067bd..e1bfbfb`)
**Strategic context**: Hotfix on top of Sprint 2 (`add-mcp-client`, shipped same day at `70d2c47..ab067bd`). Real-machine smoke after Sprint 2 ship exposed two bugs invisible to 4-slot adversarial review + 2173-test suite + gated MCP E2E. Both fixed end-to-end with full dual-channel verification.

---

## TL;DR

Sprint 2 ship-version had a "phantom modal" UX bug visible only when a user invoked an MCP tool not in `tools_requiring_approval` under `approval` tier on the Web channel: the runner emitted `ToolApprovalRequest` for every approval-tier call, while the channel hook independently fast-path-approved it — user saw a modal but clicks were ignored because the tool had already executed. Sprint 1 builtin tools (`bash`/`write`/`edit`) all happened to be in the default list, so the bug was masked for 6 weeks. Sprint 2's 14 MCP tools triggered first exposure.

This hotfix replaces in-hook fast-path with **runner-side partition**: approval-tier calls go through `actually_gated` (forced-tier OR `hook.should_gate(name)`) vs `auto_approved` (audit log only, no event, no hook). Hook protocol gains `should_gate(name) -> bool` predicate. Web + Feishu hooks now only receive actually-gated calls; non-gated calls audit-trail with `decided_by="auto:not-gated"` directly from the runner.

* **3 commits** atomic + reviewed.
* **+8 tests, 0 regression** (final: 2181 passed, 32 skipped — baseline 2173 from Sprint 2 ship).
* **4-slot post-impl review** (Oracle architecture + Ground-Truth + Cross-Consistency + Oracle adversarial) — found 2 🔴 still-broken paths in the initial hotfix, both fixed in `e1bfbfb`.
* **Real-machine dual-channel smoke verified** — Web (modal) + Feishu (CardKit) both gating correctly, both auto-approving correctly, both denying correctly.
* **Methodology blind-spot 13 documented** — "event-flow vs decision-flow lockstep" (event emit precondition must lock with decision flow; ad-hoc downstream "save-the-day" code is not architecture).

---

## Bugs fixed

### Bug #1 — `/health` endpoint masked by SPA catch-all mount (commit `5dae41d`)

Pre-existing route ordering bug from Sprint 1 (or earlier): `@app.get("/health")` was defined AFTER `app.mount("/", SPAStaticFiles(...))`. FastAPI matches routes in registration order, so `curl /health` returned the React `index.html` instead of the JSON liveness probe. Sprint 2 added the new `mcp` advisory field to `/health` (5eb405d) which was never reachable from outside the test suite.

**Why automated tests didn't catch it**: `TestClient`-based tests bypass the SPA mount because `StarletteStaticFiles` only serves real on-disk files. In dev/CI the React `web/dist/` may not exist yet, so the mount silently no-ops and `/health` resolves correctly. Real-machine smoke (after `npm run build`) is the only path exercising the full mount chain — and that's where Sprint 2's `curl /health` revealed the bug.

**Fix**: pure code re-order in `app.py` `create_app()` — move `@app.get("/health")` definition above the `if settings.channels.web.enabled` block (which registers the SPA mount). 28 lines of diff, zero logic change.

### Bug #2 — Web channel "phantom modal" + Feishu latent forced_tier-bypass (commit `8d167f2` + review patch `e1bfbfb`)

#### Root cause (event-flow vs decision-flow desync)

```
runner.py L737-746 (Sprint 2 ship version)
  └─ for every call_tier=="approval" call → yield ToolApprovalRequest
     ├─ Web: chat.py:552-563 forwards as tool.approve_request WS event → SPA shows modal
     └─ Feishu: handler.py:628-629 `elif ToolApprovalRequest: continue` (drops event)

  └─ then call hook.before_tool_execution() with the SAME batch
     ├─ Web hook L60-72: fast-path "approve" if name not in tools_requiring_approval
     └─ Feishu hook L65-84: same fast-path

  Result on Web: modal stays visible, hook returns "approve", runner executes
  tool. User clicks Approve/Deny — both no-ops.

  Result on Feishu: incidentally protected by handler.py:629 `continue` (drops
  the runner event before any CardKit posting). But the Feishu hook had the
  SAME fast-path with the SAME latent forced_tier-bypass-list bug — would
  have violated Sprint 2 spec invariant if anyone configured a server with
  forced_tier="approval" + tool name not in tools_requiring_approval.
```

Sprint 1 builtin-only era: `tools_requiring_approval` default `["bash", "write", "edit"]` covered every Sprint 1 write-class builtin tool, fast-path almost never fired. Sprint 2 added 14 MCP filesystem tools, none in the list — fast-path fired on every MCP read.

#### Architectural fix (Oracle-reviewed)

1. **`ToolApprovalHook` Protocol** gains synchronous predicate:
   ```python
   def should_gate(self, tool_name: str) -> bool: ...
   ```
   MUST be sync. MUST NOT be called by the runner when `tier_source == "forced-by-server-config"` (forced-tier calls are unconditionally gated per Sprint 2 spec invariant).

2. **Runner per-call eval** (`src/pyclaw/core/agent/runner.py` L702-770) replaces single `approval_subset` list with partition:
   - `actually_gated` ← forced-tier-by-server-config OR `hook.should_gate(canonical_name)` returns True
     - emits `ToolApprovalRequest` event, calls `hook.before_tool_execution()` for this subset
   - `auto_approved` ← everything else
     - emits `_emit_runner_audit(decided_by="auto:not-gated")` directly
     - emits NO event, calls NO hook

3. **Web + Feishu hooks** (`src/pyclaw/channels/web/tool_approval_hook.py`, `src/pyclaw/channels/feishu/tool_approval_hook.py`):
   - implement `should_gate(name)` (1-liner: `return name in self._settings.tools_requiring_approval`)
   - remove in-hook fast-path block (was duplicating runner's job and firing AFTER user-visible event)

#### Review patch invariants (commit `e1bfbfb`)

4-slot post-impl review (Oracle + Ground-Truth + Cross-Consistency + Adversarial) on `8d167f2` found two 🔴 still-broken paths:

- **🔴 hook=None + forced_tier="approval" → silent execution bypass**:
  In CLI/headless deployments without a channel hook, the runner's `is_forced` branch unconditionally added the call to `actually_gated`, but the subsequent `if actually_gated and hook is not None` guard fell through. Tool executed without approval, violating Sprint 2 spec invariant. **Fix**: new branch denies all entries with `decided_by="auto:no-hook"` audit + denial message.

- **🔴 `hook.should_gate()` exception kills entire dispatch**:
  No try/except around the call. Any exception propagated up the async generator, killing all tool calls in the iteration AND likely the run. **Fix**: wrap in try/except with **fail-closed** (treat as gated when exception raised) — better to over-prompt than silently auto-approve. Logs warning with `exc_info` for operator visibility.

Both 🔴 confirmed by **two independent slots** (Oracle architecture + Oracle adversarial), per spec-review-methodology-lessons §finding-alignment criterion (≥2 slots = MUST fix). Other 🟡 findings (sequential await latency, canonical-name config pitfall, doc drift) found by single slots — properly classified non-blocking known issues.

---

## Files changed

| File | Purpose | Diff |
|---|---|---|
| `src/pyclaw/app.py` | Move `/health` before SPA mount (Bug #1) | +28/-28 (re-order only) |
| `src/pyclaw/core/hooks.py` | Add `should_gate` to `ToolApprovalHook` Protocol + Sprint 2.0.1 docstring contract | +36/-2 |
| `src/pyclaw/core/agent/runner.py` | Runner per-call partition + hook=None deny + should_gate exception fail-closed | +52/-12 |
| `src/pyclaw/channels/web/tool_approval_hook.py` | Implement `should_gate`; remove in-hook fast-path | +5/-23 |
| `src/pyclaw/channels/feishu/tool_approval_hook.py` | Implement `should_gate`; remove in-hook fast-path | +5/-19 |
| `tests/unit/core/test_tool_approval.py` | `TestSprint201HotfixActuallyGatedPartition` class — 6 partition tests including hook=None deny + should_gate exception fail-closed | +166 |
| `tests/unit/channels/web/test_tool_approval_hook.py` | Replace `TestAutoApproveUngatedTools` with `TestShouldGate` (3 sync-predicate tests) | +30/-32 |
| `tests/unit/channels/feishu/test_tool_approval_hook.py` | Same shape as Web | +24/-26 |

Untracked (in `.gitignore` per project convention but kept on disk for audit-trail):

- `openspec/changes/archive/2026-05-16-add-mcp-client/specs/tool-approval-tiers/spec.md` — Sprint 2.0.1 hotfix amendment (new "MUST NOT call should_gate when forced" requirement + new scenario "non-gated approval-tier calls SHALL NOT emit ToolApprovalRequest")
- `DailyWork/reviews/spec-review-methodology-lessons.md` — methodology blind-spot 13 documented
- `DailyWork/handoff/handoff-2026-05-16-mcp-approval-gate-hotfix.md` — handoff for the session that did this work

---

## Verification matrix

### Test regression

```
$ .venv/bin/pytest tests/ --ignore=tests/e2e -q --tb=no
2181 passed, 32 skipped in 46.49s
```

Baseline before hotfix: 2173 passed (Sprint 2 ship). Net change: +8 tests (6 new partition tests in `TestSprint201HotfixActuallyGatedPartition` + 3 new `should_gate` tests in Web/Feishu - 5 deleted in-hook fast-path tests = +8 net).

LSP diagnostics clean on all 5 changed source files (`hooks.py`, `runner.py`, both hooks, `app.py`).

### 4-slot post-impl review

| Slot | Subagent | Verdict | 🔴 found | 🟡 found |
|---|---|---|---|---|
| 1 — Architecture | Oracle | READY-TO-SHIP (after fix) | 1 (should_gate exception unhandled) | 0 |
| 2 — Ground-Truth | explore | 8/8 CLAIMs VERIFIED | 0 | 0 |
| 3 — Cross-Consistency | explore | All 5 CHECKs aligned | 0 | 2 (cosmetic naming + doc juxtaposition) |
| 4 — Adversarial | Oracle | 5 ✅ defended / 2 🔴 / 2 🟡 | 2 (hook=None bypass + should_gate exception) | 2 (latency, config UX) |

**Alignment verdict**: 2 🔴 confirmed by 2 independent slots (#3 by both Oracle slots) → both fixed in `e1bfbfb`. Single-slot 🟡 findings → non-blocking known issues.

### Real-machine dual-channel smoke

| # | Channel | Tier | Tool | Pre-hotfix expected | Post-hotfix actual | Pass |
|---|---|---|---|---|---|:-:|
| W1 | Web | approval | `fs:list_directory` (non-gated MCP) | modal (phantom) | no modal, tool executes silently, audit `auto:not-gated` | ✅ |
| W2 | Web | approval | `bash` (gated builtin) | modal | modal, click Approve → bash runs | ✅ |
| F1 | Feishu | approval (session override via `/tier approval`) | `fs:list_directory` (non-gated MCP) | event dropped by handler.py:629 (incidental) | no CardKit, tool executes, audit `auto:not-gated` | ✅ |
| F2a | Feishu | approval | `bash` (gated builtin) | CardKit fires | CardKit, countdown updates every 5s, click Approve → frozen terminal card + bash runs | ✅ |
| F2b | Feishu | approval | `bash` (gated builtin) | CardKit fires | CardKit, click Deny → frozen terminal card + bash NOT run, agent gets denial message | ✅ |

5/5 smoke pass. Both channels' approval flows fully validated end-to-end against a real LLM + real MCP filesystem server (`npx -y @modelcontextprotocol/server-filesystem /tmp` running as stdio subprocess).

---

## Pre-existing issues surfaced during smoke (not Sprint 2.0.1 scope)

### A. `npx` registry ETIMEDOUT on cold network

When PyClaw's MCP `connect_timeout_seconds: 30` raced with `npx -y @modelcontextprotocol/server-filesystem` doing a registry probe on every spawn, the probe timed out (China-region ↔ npmjs.org common). MCP server failed to start. PyClaw's non-blocking startup invariant correctly survived (server kept running with `connected=0 failed=1`).

**Workaround applied** (in `configs/pyclaw.json`, `.gitignored`): change `args` from `["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]` to `["--prefer-offline", "-y", "@modelcontextprotocol/server-filesystem", "/tmp"]`. The `--prefer-offline` flag uses local `~/.npm/_npx/` cache when registry is unreachable. Boot time dropped from 30s timeout to <1s.

**Recommendation for `docs/{en,zh}/mcp.md`**: document `--prefer-offline` as the recommended flag for npx-based MCP servers in regions with unreliable registry access.

### B. SPA bundle stale across feature branches

Web smoke initially failed because `web/dist/assets/index-b7IG9rrV.js` was built 2026-05-15 14:53 — *before* commit `39263b7` (2026-05-15 23:17) added `<PermissionTierDropdown />` to ChatArea.tsx. Browser ran an 8-hour-old SPA bundle that lacked the tier selector entirely.

**Fix**: `cd web && npm run build` produced `index-D3aPka3t.js` containing the missing component. Browser hard-reload ($\mathcal{}+\Shift+R$) loaded the new bundle and the dropdown appeared.

**Recommendation**: pre-commit hook or CI step that diffs `web/src/` vs `web/dist/` build hash and warns on staleness. Alternatively, `make worker1` / `scripts/start.sh` should auto-build SPA on startup if `web/dist/` is older than `web/src/`.

### C. MCP SDK `prompts/resources Method not found` warnings

Filesystem server only implements `tools` capability. SDK probes all 3 capabilities (`tools`, `prompts`, `resources`) on connect, logs WARNING for unimplemented ones. Harmless — already documented as known-noise. Three mitigation options: (1) do nothing, (2) suppress logger `root` at WARNING for `Could not fetch`, (3) MCP SDK upstream filter probe responses. Decision: do nothing (signal-to-noise tradeoff favors visibility for real probe failures).

---

## Methodology evidence (blind-spot 13)

This is the first time PyClaw's adversarial-review methodology is empirically validated against a bug invisible to all of:

- 4-slot pre-implementation review (v1→v4 GATE pass)
- 4-slot post-implementation review (Oracle implementation review READY-TO-CONTINUE)
- 2173 unit + integration tests
- Gated real-MCP E2E (`PYCLAW_TEST_MCP=1`)

The bug only surfaced on **real-machine UX** because Sprint 1 spec L66 ("ToolApprovalHook Protocol SHALL NOT change") was true at the type-signature level but missed the **emit-precondition contract**: spec never declared *when* `ToolApprovalRequest` events should be emitted relative to *when* hook decisions happen. The two flows decoupled, runner emitted unconditionally, hook fast-path-approved silently. Web rendered the phantom modal; Feishu was incidentally saved by `handler.py:629 continue` (added in Sprint 1 as ad-hoc save-the-day code, not as architecture).

Documented as **methodology blind-spot 13** — "Event 流和 Decision 流没有 lockstep 契约" — in `DailyWork/reviews/spec-review-methodology-lessons.md`. Adds new review checklist items:

- For every user-visible event, list emit point + decision point — verify lockstep (same code, same condition).
- Adversarial slot prompt template: "Under what condition is this event emitted but user action ineffective?"
- Spec-level invariant: every user-visible event MUST declare its emit-precondition explicitly (not "implementation detail of the channel hook").

This complements existing blind-spot 10 (mock hides wiring bug) + blind-spot 11 (cross-language drift). Common root cause: **review default is "段-内" thinking — analyzing one piece of code's self-consistency — and does not actively trace event/state lifecycles end-to-end**.

---

## Lessons for future sprints

1. **Real-machine UX smoke is non-negotiable**. The 4-slot review caught 0 of the 2 actual user-impacting bugs. Tests caught 0. Only "boot it, click it, look at the screen" caught both. PyClaw should require a smoke checklist before any user-facing change ships.

2. **Stale SPA bundle is a recurring risk** (`web/dist/` vs `web/src/`). The Web + Feishu split deploy means feature branches that touch React UX must explicitly include a `npm run build` step or risk shipping stale frontends.

3. **`--prefer-offline` for any external CLI MCP server**. Network-dependent stdio subprocess spawns are brittle in regions with intermittent registry access. The hotfix demonstrates that PyClaw's non-blocking startup correctly survives this — but UX is a regression (tools missing) that operators would not notice without `/health` introspection.

4. **`forced_tier` + `tools_requiring_approval` interaction is the most subtle invariant in tool-approval-tiers spec**. The Sprint 2 spec scenarios covered all the "forced wins / forced loses" cases but missed the "what about non-list tools?" emit precondition. Future MCP-server permission features (per-tool glob, role-based gating) should explicitly enumerate emit preconditions for every event class they introduce.

5. **`should_gate` predicate pattern is reusable**. Any hook protocol where (a) hook does heavy async work and (b) runner needs sync gating decision before emitting user-visible events should follow this split. Candidates: future `MemoryGateHook` (sync `should_search()` + async `before_search()`), `RateLimitHook`, `BillingGateHook`.

---

## Final checklist

- [x] All 3 commits pushed to `origin/main`: `5dae41d`, `8d167f2`, `e1bfbfb`
- [x] 2181 passed / 32 skipped (no regression)
- [x] LSP diagnostics clean on changed source files
- [x] 4-slot post-impl review with finding-alignment cycle complete
- [x] Real-machine dual-channel smoke 5/5 pass
- [x] Spec amendment archived in `openspec/changes/archive/2026-05-16-add-mcp-client/specs/tool-approval-tiers/spec.md` (.gitignored, on disk)
- [x] Methodology blind-spot 13 documented in `spec-review-methodology-lessons.md`
- [x] Hotfix smoke report (this document)

Sprint 2.0.1 closed. Next: Sprint 3 (`add-knowledge-base`) per ROADMAP, or selected backlog item.
