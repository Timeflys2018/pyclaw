# Changelog

All notable changes to PyClaw are documented here. The format follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/) and the project does not yet
publish numbered versions; entries are dated and grouped by OpenSpec change.

## [Unreleased]

### `fix-tool-approval-and-permission-tiers` — Sprint 1

> **⚠️ Behaviour change — required reading for operators upgrading from
> earlier builds.** Until this change, `ToolApprovalHook` was declared but
> **never injected** in the runtime. Web users implicitly ran in YOLO mode —
> any `bash` / `write` / `edit` tool call executed without prompting,
> regardless of `toolsRequiringApproval` config (which was dead code).
>
> After this change, the hook is wired end-to-end. Web users will see a tool
> approval modal for tools listed in `toolsRequiringApproval` (default
> `["bash", "write", "edit"]`) when in `approval` tier (also the new
> default). Operators who want to preserve the old auto-execute behaviour
> have two options:
>
> 1. Set `channels.web.defaultPermissionTier: "yolo"` in `pyclaw.json`
>    (not recommended for production; documents the previous reality)
> 2. Set `channels.web.toolsRequiringApproval: []` (empty list — also
>    skips the approval flow under `approval` tier)
>
> See [docs/en/permissions.md](./docs/en/permissions.md) for the full
> three-tier model.

#### Added

- **Three permission tiers** — `read-only` / `approval` / `yolo`. User picks
  per turn via `chat.send.tier`; the frontend persists the choice in
  `localStorage`.
- **`tool_class` discriminator** on every built-in tool (`read` /
  `memory-write-safe` / `write`). The runner uses it to auto-deny
  write-class tools under `read-only` tier without invoking the hook.
- **`WebToolApprovalHook`** — concrete `ToolApprovalHook` for the Web
  channel using `asyncio.Event` (not polling) per design D6. Posts
  `tool.approve_request` events, awaits the user's response with a
  configurable timeout, and resolves to `approve` / `deny`.
- **`FeishuToolApprovalHook` + CardKit interactive card** — schema 2.0
  card with Approve / Deny buttons, 60-second countdown patched every
  5 s, terminal-state replacement on decision/timeout. Originator-only
  authorization: only the user who triggered the agent can resolve the
  decision; other group members see a toast and the card stays unchanged.
- **`AuditLogger`** — every decision (auto or user-driven) emits one
  INFO-level JSON line on `pyclaw.audit.tool_approval` logger with
  `decided_by` ∈ `auto:not-gated` / `auto:read-only` / `auto:timeout` /
  `auto:post-failed` / `<user-id>`.
- **Web frontend** — three tier pills in the input footer, `⌘K` palette
  actions (`Switch to Read-Only / Approval / YOLO mode`), tier badge in
  the existing tool approval modal.
- **Bilingual docs** — `docs/{en,zh}/permissions.md` covering the model,
  configuration, audit log, recovery, and follow-ups.

#### Changed

- `WebSettings` / `FeishuSettings` gain `defaultPermissionTier`,
  `toolApprovalTimeoutSeconds`, and (Feishu) `toolsRequiringApproval`.
  Existing configs without these fields use safe defaults
  (`approval`, `60`, `["bash", "write", "edit"]`).
- `WebSettings.toolsRequiringApproval` default changed from
  `["bash", "write"]` to `["bash", "write", "edit"]` to match the spec.
- `ChatSendMessage` accepts an optional `tier` field; invalid values are
  rejected at parse time.
- `RunRequest` accepts an optional `permission_tier_override`.
- `ToolApprovalHook.before_tool_execution` signature now accepts `tier`
  as a third positional argument. **Backward-compatible at runtime**
  (Protocol is `@runtime_checkable` and only checks method names) but
  existing custom hook implementations should add the parameter.
- `SessionQueue.set_approval_decision` now signals a per-decision
  `asyncio.Event` (via new `create_pending` API). Old polling readers
  still work; the new event-driven path also fixes K15.1 (orphan dict
  growth on session reset).

#### Fixed

- **K15** — `ToolApprovalHook` is now actually injected in the runtime
  (Web + Feishu channels). The protocol is no longer dormant.
- **K15.1** — `SessionQueue._approval_decisions` lifecycle: `reset()`
  now signals all outstanding pending entries with `approved=False`
  before clearing storage, preventing orphan awaiters across resets.

#### Documentation

- `README.md` / `README_CN.md`: replaced the previous ✅ "tool approval"
  status overclaim with an accurate description; added a dedicated
  `Tool Approval` row in the status table.
- `docs/{en,zh}/configuration.md`: documented the three new config fields
  with defaults.
- New `docs/{en,zh}/permissions.md`.

#### Tests

- 76 new tests across unit and integration:
  - `test_tool_class_registration.py` (9)
  - `test_audit_logger.py` (11)
  - `test_tool_approval_hook.py` (Web, 10)
  - `test_protocol_tier_field.py` (8)
  - `test_approval_registry.py` (Feishu, 7)
  - `test_approval_card.py` (8)
  - `test_card_callback.py` (5)
  - `test_tool_approval_hook.py` (Feishu, 9)
  - `test_tool_approval_e2e.py` (5)
  - Existing `test_tool_approval.py` updated with 4 new tier tests
- All tests run under `pytest tests/ --ignore=tests/e2e`. Final count:
  **2015 passed, 30 skipped** (vs 1939 baseline → +76 net).

#### Sprint 1 follow-ups deliberately deferred

| ID | Item | Rationale |
|----|------|-----------|
| TA1 | Per-tool glob (`bash:rm -rf *: deny`) | Sprint 1.1 — simple list ships first, glob layered on top once stable |
| TA2 | Audit log persistence (Redis / SQLite) | Sprint 1.1 if needed — structured logger covers ops grep today |
| TA3 | Per-user permission profile | Pairs with multi-tenancy; do together |
| TA4 | Approval rule engine / policy DSL | Premature for current scale |
| TA5 | Approval analytics dashboard | Requires TA2 first |

See [`DailyWork/planning/KNOWN-ISSUES.md`](./DailyWork/planning/KNOWN-ISSUES.md)
for the full follow-up tracker.
