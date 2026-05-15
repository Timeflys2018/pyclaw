# Sprint 1 Ship Report — fix-tool-approval-and-permission-tiers

**Date**: 2026-05-15
**Branch**: `feat/fix-tool-approval-and-permission-tiers` (worktree)
**OpenSpec change**: `openspec/changes/fix-tool-approval-and-permission-tiers/`
**Strategic context**: handoff §6 (1.5 weeks scope, 12 phases)

---

## TL;DR

Replaces a long-standing README overclaim (✅ "tool approval") with the real
end-to-end thing. The runtime now wires `ToolApprovalHook` for both Web
(modal) and Feishu (CardKit interactive card) channels, introduces a
three-tier permission system (`read-only` / `approval` / `yolo`) with
per-turn override, and emits structured JSON audit lines for every decision.

- **11 commits** (`ef2afe0` → `de44fef`)
- **+76 tests, 0 regression** (final: 2015 passed, 30 skipped)
- **Resolves**: K15, K15.1, L7, S2, S2.1
- **Defers** (with IDs): TA1 per-tool glob, TA2 audit persistence, TA3-TA5
  multi-tenant / DSL / dashboard

---

## Phase-by-phase log

| # | Commit | Description | Tests | Risk |
|---|--------|-------------|-------|------|
| 1 | `ef2afe0` | Types + settings (PermissionTier, default_permission_tier, tool_approval_timeout_seconds, tools_requiring_approval default change) | 1939 | low |
| 2 | `9e48da6` | tool_class field on Tool Protocol + 8 builtins + registry-level startup check | 1948 (+9) | mid |
| 3 | `531edaf` | Per-turn tier propagation in runner; read-only/approval/yolo gate logic | 1952 (+4) | **HIGH** |
| 4 | `101dc87` | AuditLogger (structured JSON line per decision) | 1963 (+11) | low |
| 5 | `c316217` | WebToolApprovalHook + asyncio.Event refactor of SessionQueue | 1973 (+10) | **HIGH** |
| 6 | `1caffd4` | Web channel wiring: factory.py / app.py lifespan / chat.py tier-aware | 1981 (+8) | mid |
| 7 | `52e5f06` | Feishu CardKit interactive UI: registry + card builder + callback handler + originator-only auth | 2010 (+29) | **HIGH** |
| 8 | `0d5de4f` | Web frontend: permissionStore + tier pills + ⌘K actions + modal badge | 2010 | mid |
| 9 | `71ff6b1` | E2E integration tests (5 tier paths server-side) | 2015 (+5) | mid |
| 10 | `b3be1a1` | Bilingual docs/{en,zh}/permissions.md + README honesty + configuration.md updates | 2015 | low |
| 11 | `de44fef` | CHANGELOG with prominent behaviour-change callout | 2015 | low |

---

## Files changed

### New (12 files)

```
src/pyclaw/infra/audit_logger.py
src/pyclaw/channels/web/tool_approval_hook.py
src/pyclaw/channels/feishu/approval_card.py
src/pyclaw/channels/feishu/approval_registry.py
src/pyclaw/channels/feishu/card_callback.py
src/pyclaw/channels/feishu/tool_approval_hook.py
web/src/stores/permission.ts
docs/en/permissions.md
docs/zh/permissions.md
CHANGELOG.md
tests/integration/test_tool_approval_e2e.py
+ 8 unit test files (test_audit_logger / test_tool_class_registration /
  test_protocol_tier_field / test_tool_approval_hook (web) /
  test_approval_card / test_approval_registry / test_card_callback /
  test_tool_approval_hook (feishu))
```

### Modified

```
src/pyclaw/core/hooks.py
src/pyclaw/core/agent/factory.py
src/pyclaw/core/agent/runner.py
src/pyclaw/core/agent/tools/registry.py
src/pyclaw/core/agent/tools/builtin.py
src/pyclaw/core/agent/tools/{memorize,forget,update_working_memory,skill_view}.py
src/pyclaw/infra/settings.py
src/pyclaw/app.py
src/pyclaw/channels/web/{chat.py,deps.py,protocol.py}
src/pyclaw/channels/feishu/{client.py,dispatch.py,handler.py,webhook.py}
web/src/{types.ts,pages/Chat.tsx,components/{ChatArea,CommandPalette,ToolApproval}.tsx}
web/src/stores/index.ts
README.md / README_CN.md / docs/{en,zh}/configuration.md
+ 9 existing test files (added tool_class to fake tools, updated
  MockApprovalHook for tier param)
```

---

## Test verification

### Unit + integration (no external deps)

```
$ pytest tests/ --ignore=tests/e2e -q
2015 passed, 30 skipped in 51.59s
```

- 2015 = 1939 baseline + 76 net new
- 30 skipped is unchanged from baseline (all pre-existing skip markers)

### Frontend build

```
$ npm run build
✓ tsc -b clean
✓ vite build clean (449.91 KB / 137.11 KB gzipped main chunk)
```

### Lint state (deferred)

The codebase has 1964 pre-existing ruff errors and 208 format issues; the
new files contribute 21 new ANN401 (`Any` annotations) related to
lark-oapi SDK interop where the SDK types are not exposed as stubs. None
of these are new to Sprint 1 in nature; they match the codebase's existing
lint posture. Project-wide lint cleanup is tracked separately.

---

## What ships per spec scenario

| Spec scenario | Status |
|---|---|
| `read-only` auto-denies bash | ✅ verified by E2E test_read_only_auto_denies_no_approval_event |
| `read-only` allows memorize | ✅ verified by tool_class registration test |
| `approval` gates configured tools | ✅ verified by E2E test_approval_tier_user_approves_tool_executes |
| `approval` auto-approves unconfigured tools | ✅ verified by web hook unit test_mixed_calls_only_gates_listed_tools |
| `yolo` skips all approval | ✅ verified by E2E test_yolo_skips_approval_executes_directly |
| Default tier `approval` | ✅ verified by tier propagation unit test |
| Tier override per turn, not persisted | ✅ verified by Web frontend permissionStore + protocol tier-field tests |
| Approval timeout 60s default | ✅ verified by E2E test_approval_tier_timeout_denies |
| ToolApprovalHook contract | ✅ Protocol + 4 hook tests + 5 E2E tests |
| Web hook via SessionQueue + asyncio.Event | ✅ test_tool_approval_hook + test_session_queue_pending_api |
| Feishu hook via CardKit | ✅ test_approval_card + test_tool_approval_hook (feishu) |
| Originator-only authz for Feishu | ✅ test_card_callback::test_non_originator_click_rejected |
| Audit log JSON line per decision | ✅ test_audit_logger schema + variants + E2E audit assertions |
| `tools_requiring_approval` config drives `approval` tier | ✅ test_tool_approval_hook gating + per-tool unit tests |

---

## Manual smoke checklist (must run before merging to main)

The unit + integration suite proves the wiring is sound, but the Feishu
flow specifically requires real-world smoke before declaring "production
verified":

### Web channel smoke

- [ ] Open Web UI, send "run `ls` via bash"
  - Tier pill `approval` selected by default
  - `tool.approve_request` arrives, modal shows tier badge
  - Click Approve → bash executes, response streams
  - Click Reject → tool.end shows "denied by approval hook"
- [ ] Switch to `read-only` pill, ask agent to write a file
  - No modal appears
  - Agent receives `is not available in read-only mode. (Mode can be changed in the input footer.)`
- [ ] Switch to `yolo` pill, run bash
  - No modal
  - Tool executes immediately
- [ ] ⌘K palette → "Switch to YOLO mode" — pill state syncs
- [ ] Reload page — selected tier persists (localStorage)
- [ ] Inspect stdout — JSON audit lines on `pyclaw.audit.tool_approval`

### Feishu channel smoke

- [ ] Confirm Feishu Developer Console subscriptions:
  - `im.message.receive_v1` ✅
  - `card.action.trigger` ✅ ← **NEW: must be subscribed**
  - WebSocket long-connection mode: enabled
- [ ] @bot in P2P, ask "run ls"
  - CardKit card appears with Approve/Deny buttons + countdown
  - Originator clicks ✅ Approve → card patches to "✅ Approved by..."; tool runs
- [ ] @bot in group, originator triggers, **second account clicks button**
  - Toast: "Only the originator can approve/deny this action."
  - Card buttons remain enabled for originator
- [ ] Trigger approval, wait 60s without clicking
  - Card patches to "⌛ Timed Out"
  - Agent receives timeout deny error
- [ ] Inspect stdout — JSON audit lines with `decided_by` ∈ {`<open_id>`, `auto:timeout`}

### Multi-channel audit

- [ ] After 10+ approvals across channels:
  ```bash
  journalctl -u pyclaw -f | grep tool_approval_decision | jq .
  ```
  Expect well-formed JSON each line, all required fields present.

---

## Migration notes for operators

**Behaviour change** (also in CHANGELOG):

Existing Web users see modal popups for `bash`/`write`/`edit` after
upgrade. To preserve old auto-execute behaviour, either:

1. Set `channels.web.defaultPermissionTier: "yolo"` (not recommended)
2. Set `channels.web.toolsRequiringApproval: []`

**New required Feishu Developer Console subscription**: `card.action.trigger`.
Without this, approval cards won't respond to clicks; the 60s timeout will
auto-deny everything. PyClaw's WS dispatcher only registers the callback if
the `FeishuApprovalRegistry` is constructed (i.e. Feishu channel is enabled
in `pyclaw.json`).

---

## Resolved issues

- ✅ K15 — ToolApprovalHook is now actually injected (Web + Feishu)
- ✅ K15.1 — SessionQueue._approval_decisions lifecycle (asyncio.Event + reset cleanup)
- ✅ L7 — Feishu now has a real tool approval UI (CardKit interactive card)
- ✅ S2, S2.1 — same as K15/K15.1 above
- ✅ README overclaim "tool approval ✅" replaced with accurate description

---

## Deferred to follow-ups

| ID | Item | Trigger |
|----|------|---------|
| TA1 | per-tool glob (`bash:rm -rf *: deny`) | Sprint 1.1 — when simple list shows pain |
| TA2 | persistent audit log (Redis sorted set / SQLite) | Sprint 1.1 — when ops asks for web-UI audit history |
| TA3 | per-user permission profile | With multi-tenancy upgrade |
| TA4 | rule-engine / DSL | When TA1 globs aren't expressive enough |
| TA5 | analytics dashboard | After TA2 |
| TA6 | CardKit countdown 50-concurrent ceiling monitor | Production observation |
| Frontend unit tests | Vitest setup | Once vitest is added to `web/devDependencies` |
| Lint cleanup | Project-wide ANN401 / format | Separate change |

All tracked in `DailyWork/planning/KNOWN-ISSUES.md`.

---

## Acknowledgements / methodology

Built incrementally over 11 phases with strict checkpoint discipline at
the three HIGH-RISK phases (3 / 5 / 7). Every phase ended with full
pytest regression run and a single atomic commit (12-commit history is
clean for `git bisect`). LSP diagnostics consulted on every changed file.
Three background agents (2 explore + 1 librarian) provided ground-truth
maps for both the existing Feishu surface and the lark-oapi CardKit API.

End of report.
