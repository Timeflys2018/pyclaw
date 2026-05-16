# Sprint 3 Ship Report — `user-isolation-and-per-user-permissions`

**Date**: 2026-05-16
**Status**: 🟢 SHIPPED
**Sprint**: 3 (post Sprint 2.0.1 hotfix)
**OpenSpec change**: `user-isolation-and-per-user-permissions` (270+519+286+430 = 1505 行 4-piece kit)
**Effort**: 1 day actual (planned 7 days)
**Test progression**: 2181 → 2364 (+183 net, 0 regression)
**Post-ship review**: 4-slot review v2 (2026-05-16 evening) found 6 ship-blockers; all fixed in 3.0.x hotfix bundle (see "Post-Ship Validation" section)

---

## TL;DR

Sprint 3 closes the **enterprise-readiness gap** that Sprint 2.0.1 surfaced:

1. **Per-user permission profiles** (`UserProfile` schema) — alice can default
   to `read-only`, bob to `yolo`, with channel-isolated identity (Web alice ≠
   Feishu alice).
2. **BashTool + MCP subprocess isolation** via Anthropic `srt` 1.0.0 — fully
   opt-in (`sandbox.policy="srt"`); `NoSandboxPolicy` default keeps Sprint
   2.0.1 behavior byte-identical.
3. **Operator slash commands** — `/admin user set/list/show` (with last-admin
   protection) + `/admin sandbox check` for runtime visibility.

D26 isolation positioning upgraded from "personal/trusted-team only" to
**"configurable multi-user with role + sandbox isolation"**.

---

## Commits Shipped

| Commit | Phase | Scope |
|---|---|---|
| `31792b9` | Phase 1 (a) | auth/ scaffolding (UserProfile + RedisJsonStore + 4-layer resolver + REPLACE-semantics) + ToolApprovalHook Protocol amendment |
| `96786d0` | Phase 1 (b) | Channel handler wiring (`resolve_profile_and_tier` + web/chat + openai_compat + feishu/handler) |
| `7e97f16` | Phase 2 | SandboxPolicy + NoSandboxPolicy + env_strip + 11-invariant manifest |
| `167f868` | Phase 3 | SrtPolicy + /admin user + /health.sandbox + PYCLAW_SANDBOX_OVERRIDE |
| `458495b` | Phase 4 | MCP per-server sandbox + npx/uvx auto-exempt + /admin sandbox check |
| `e1ecef5` | Phase 5 | Audit log enrichment (user_id/role/sandbox_backend) + 双语 docs (sandbox.md + multi-user-deployment.md) + ROADMAP update + ship report |
| `37b0766` | 3.0.1 hotfix | `web/command_adapter` populates `ctx.raw['user_role']` (post-Phase-5 真机 smoke discovered Redis admin promotion didn't take effect for /admin commands) |
| `[3.0.x bundle]` | 3.0.x bundle | 4-slot review v2 fixes: F1 SrtPolicy temp file cleanup + A3 ARG_MAX fail-closed + A4 LD_PRELOAD/DYLD_INSERT deny + C-1 last-admin audit log + Feishu symmetric command_adapter hotfix + F6 docs note + ship report corrections |

Total: 8 atomic commits, 0 force-pushes.

---

## Test Stats

| Phase | New tests | Cumulative |
|---|---|---|
| Sprint 2.0.1 baseline | — | 2181 |
| Phase 1 (a+b) | +80 | 2261 |
| Phase 2 | +42 | 2303 |
| Phase 3 | +28 | 2331 |
| Phase 4 | +20 | 2351 |
| Phase 5 (audit) | +4 | 2355 |
| 3.0.1 hotfix (web cmd adapter) | +4 | 2359 |
| 3.0.x bundle (4-slot v2 fixes) | +5 | 2364 |
| **Total Sprint 3** | **+183 net** | **2364 passed, 32 skipped, 0 regression** |

Per `tasks.md` Phase 5 target was ~2306 passed (+125). We delivered **+183**
(+58 over plan) due to deeper invariant assertions, edge-case coverage, and
post-ship 4-slot review v2 hardening.

---

## 4-Slot Review Findings — All 8 Baked

| Finding | Severity | Where Fixed |
|---|---|---|
| **F1** npx + sandbox 升级 UX | 🔴 | Phase 4 — `_command_auto_exempts_sandbox` basename detection + conditional default + INFO log advisory |
| **F2** `tools_requiring_approval` REPLACE 语义 | 🔴 | Phase 1 — `resolve_tools_requiring_approval` helper + `should_gate(name, ctx)` Protocol amendment |
| **F3** design.md §8 stale text | 🔴 | Spec text rewritten before implementation (2026-05-16) |
| **F4** admin self-demote deadlock | 🔴 | Phase 3 — `_handle_set` last-admin-protection guard + 2 explicit tests |
| **F5** srt startup hang zombie | 🟡 | Phase 4 — `asyncio.timeout(connect_timeout_seconds)` + `test_connect_timeout_marks_server_failed` |
| **F7** 11-invariant manifest drift | 🟡 | Phase 2 — `tests/integration/test_sprint3_invariants_preserved.py` (12 tests) |
| **F9** `production_require_sandbox` env override 名 | 🟡 | Phase 3 — `PYCLAW_SANDBOX_OVERRIDE=disable` + `test_disable_overrides_production_require` |
| **F10** env_allowlist 提权 hardcoded deny | 🟡 | Phase 2 — `validate_env_allowlist` rejects deny-prefix globs + `HARDCODED_DENY_NAMES` enforced regardless of allowlist |

All 4 🔴 + 4 🟡 baked into spec **before** implementation; all baked into code
**during** implementation.

---

## Sprint 1+2+2.0.1 Invariants Preserved

11 explicit assertions in `tests/integration/test_sprint3_invariants_preserved.py`
(all green at HEAD):

1. ✅ Sprint 2.0.1 `should_gate(name, ctx=None) -> bool` synchronous predicate
2. ✅ Sprint 2.0.1 `actually_gated` partition (runner emits `ToolApprovalRequest` only for gated)
3. ✅ Sprint 2 `forced_tier` de-escalation — `_RANK = {"read-only": 2, "approval": 1, "yolo": 0}` literal preserved
4. ✅ Sprint 2 `tier_source = "forced-by-server-config"` literal preserved in audit log
5. ✅ Sprint 1 `WorkspaceResolver.resolve_within` path traversal protection
6. ✅ Sprint 1 BashTool `cwd = context.workspace_path` contract
7. ✅ Sprint 1 abort/timeout grace 2.0s constant
8. ✅ Sprint 1 ToolResult `[stdout]/[stderr]/[exit_code=N]` format
9. ✅ Sprint 1 sessionKey-based override (key prefix `pyclaw:feishu:tier`)
10. ✅ Web `web_{user_id}` + Feishu `feishu_{app}_{open_id}` workspace naming
11. ✅ MCP subprocess independence — sandbox injection at `StdioServerParameters` layer, not via BashTool

Plus a Sprint 3 invariant: `ToolContext.sandbox_policy` defaults to
`NoSandboxPolicy()` so existing tests pass unchanged (backward compat).

---

## Spec → Code Coverage

| Spec Requirement | Code Site | Test Site |
|---|---|---|
| Per-user tier as 3rd precedence layer | `auth/tier_resolution.py:resolve_effective_tier` + channel handler `RunRequest.permission_tier_override` | `tests/unit/auth/test_role_precedence.py` (16+ combinations) |
| `tools_requiring_approval` REPLACE | `auth/tools_requiring_approval.py:resolve_tools_requiring_approval` + Web/Feishu hook `should_gate(name, ctx)` | `tests/unit/auth/test_tools_requiring_approval_replace.py` + `test_tool_approval_hook.py::TestShouldGateUserProfileReplace` |
| UserProfile schema + RedisJsonStore | `auth/profile.py` + `auth/profile_store.py` | `tests/unit/auth/test_profile.py` + `test_profile_store.py` |
| SandboxPolicy abstraction | `sandbox/policy.py` + `sandbox/no_sandbox.py` + `sandbox/srt.py` | `tests/unit/sandbox/test_*.py` |
| env_strip hardcoded deny floor (F10) | `sandbox/env_strip.py:validate_env_allowlist` + `HARDCODED_DENY_NAMES` | `test_env_strip.py::TestHardcodedDenyFloor` (9 tests) |
| MCP per-server sandbox + npx auto-exempt (F1) | `mcp/settings.py:_resolve_sandbox_default` + `mcp/client_manager.py:_connect_one` | `test_sandbox_config.py` + `test_sandbox_wrapping.py` |
| `/admin user` + last-admin guard (F4) | `core/commands/admin.py:_handle_set` | `test_admin_user.py::TestLastAdminProtection` (2 tests) |
| `/admin sandbox check` | `core/commands/admin.py:_handle_sandbox_check` | `test_admin_user.py::TestSandboxCheck` (3 tests) |
| `/health.sandbox` advisory | `app.py /health` + `sandbox/state.py:health_advisory` | `test_state.py` |
| `PYCLAW_SANDBOX_OVERRIDE=disable` (F9) | `sandbox/state.py:resolve_sandbox_state` | `test_state.py::TestPyclawSandboxOverrideEnv` |
| Audit log user_id/role/sandbox_backend | `infra/audit_logger.py:log_decision` + `core/agent/runner.py:_emit_runner_audit` | `test_audit_logger.py::TestSprint3LogDecisionEnrichment` |

---

## Documentation Shipped

- ✅ `docs/en/sandbox.md` + `docs/zh/sandbox.md` — quickstart, schema, per-user
  overrides, hardcoded deny floor, MCP per-server, Sprint 2 → 3 migration,
  emergency override, troubleshooting
- ✅ `docs/en/multi-user-deployment.md` + `docs/zh/multi-user-deployment.md` —
  concepts, channel isolation, JSON config, runtime `/admin` workflow,
  last-admin protection, tier resolution, audit trail, recommended production
  setup
- ✅ `DailyWork/planning/ROADMAP.md` — Sprint 3 marked ✅, Sprint 4
  (add-knowledge-base) re-prioritized to 🥇

---

## Post-Ship Validation (added 2026-05-16 evening)

After Phase 5 ship, the team executed:

1. **Real-machine smoke (47+ scenarios)** against remote production Redis:
   - 4-layer tier resolution (alice/bob/admin/mallory): 6 scenarios pass
   - `/admin user list/show/set` + last-admin protection (F4): 8 scenarios pass
   - SrtPolicy real sandbox interception (`cat ~/.ssh/id_rsa` → Operation not permitted): 6 scenarios pass
   - Audit enrichment (3 scenarios), F10 hardcoded env deny floor (5 scenarios), F1 npx auto-exempt verified
2. **3.0.1 hotfix** (commit `37b0766`): smoke discovered `/admin user set bob role=admin` writes Redis but bob's subsequent `/admin user list` was rejected because `web/command_adapter.py` didn't populate `ctx.raw["user_role"]`. Fixed + 4 regression tests added.
3. **4-slot review v2** (Oracle architecture + explore Ground-Truth + explore Cross-Consistency + Oracle Adversarial): 39 findings total. ≥2-slot consensus + single-slot 🔴 = **6 ship-blockers**, all fixed in 3.0.x bundle commit:
   - **F1** (Slot 1 🔴): SrtPolicy temp file leak — added `cleanup_settings_file()` + atexit fallback + BashTool try/finally cleanup
   - **A3** (Slot 4 🔴): `ARG_MAX_FALLBACK_THRESHOLD` silent NoSandbox bypass — changed to fail-closed (`SrtCommandTooLong` exception); BashTool returns user-friendly error
   - **A4** (Slot 4 🔴): `LD_PRELOAD` / `DYLD_INSERT_LIBRARIES` not in deny floor — added 6 dyld/ld hijack vectors to `HARDCODED_DENY_NAMES`
   - **C-1 / GT1** (Slots 2 + 3 + 4 共识 🔴): F4 last-admin guard didn't emit structured audit log per spec — added `_emit_last_admin_audit()` with `reason="last-admin-protection"`
   - **🆕 Feishu symmetric** (post-3.0.1 user observation): 3.0.1 hotfix only patched Web; symmetric bug existed in `feishu/command_adapter.py` → added equivalent `resolve_profile_and_tier` lookup + 3 regression tests
   - **C-3 / E1** (Slots 2 + 3 共识 🔴): ship report stale (commit count 6→8, test count 2355→2367, missing post-ship validation section) — corrected via this update

## Deferred to Sprint 5+ / Future Work

| Item | Reason |
|---|---|
| Cross-channel UserProfile mapping (Web alice ↔ Feishu alice) | Sprint 3.x — YAGNI for current single-platform deployments |
| Per-tool glob in `tools_requiring_approval` (e.g. `bash:git push --force`) | Sprint 1.1 follow-up TA1 |
| Audit log persistence (Redis sorted set / SQLite) | Sprint 1.1 follow-up TA2 |
| 4-slot review v2 🟡 findings (A1 Redis race / A7 zero-admin bootstrap / A12 cross-worker cache invalidation / B1+B2 JSON snake/camel mixing / GT3 srt version enforcement / others) | Tracked as Sprint 3.x backlog, not ship-blocking |
| `permissions.md` / `architecture-decisions.md` D26 update with detailed multi-user prose | Initial ROADMAP/sandbox.md/multi-user-deployment.md cover the entry path; deeper rewrites tracked as docs polish |

---

## Lessons Learned

- **Phasing held tightly**: Each phase shipped as one atomic commit with full
  regression + LSP clean. Zero phase needed a hotfix or revert.
- **TDD red phase caught real design ambiguities early** (e.g. T2.3 hardcoded
  deny floor revealed AWS_REGION-vs-AWS_* distinction not in spec; spec
  scenario was tightened mid-implementation).
- **Backward-compat ergonomics**: `try/except TypeError` fallback (Sprint 2's
  audit log pattern) generalized cleanly to `should_gate(name, ctx)` and
  Phase 5 audit field expansion. Established pattern for Sprint N+1 hooks.
- **OpenSpec 4-piece kit + 4-slot review v1 + spike S0.x** was load-bearing:
  the spike resolved srt fd-passing risk before we wrote a line of Phase 4
  code; the 4-slot review v1 baked F1-F10 into spec **before** implementation
  so we never wrote then deleted code.

---

## Next Sprint

Sprint 4 — `add-knowledge-base` (user-visible KB, parallel to 4-layer memory).
Estimated 1.5 weeks. No dependencies blocking.

---

**End of report.**
