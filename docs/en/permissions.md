# Permissions & Tool Approval

PyClaw lets you control how autonomously the agent acts. Three permission tiers
balance safety against velocity, and a `tools_requiring_approval` allow-list
gates the most dangerous tools.

> Why now: this replaces an earlier silent-YOLO behaviour where tool approval
> was wired through the protocol but never injected at runtime. The README
> previously called it ✅ shipped — that was overclaim. This page describes
> what is actually shipped as of Sprint 1.

## Three permission tiers

| Tier | Behaviour | Typical use |
|------|-----------|-------------|
| `read-only` | Write-class tools (`bash`, `write`, `edit`, `forget`) auto-denied **without prompting**. Read-class tools (`read`, `grep`, `glob`, `skill_view`, `update_working_memory`) and `memorize` execute freely. | Research, code-walk, brainstorming. The agent can take notes (`memorize`) but cannot mutate your workspace. |
| `approval` | Tools listed in `tools_requiring_approval` trigger the channel's approval flow (Web modal / Feishu CardKit card). Other tools auto-approve. | **Default.** Safe baseline for everyday work. |
| `yolo` | All tools auto-approve. The approval gate is effectively disabled. | Trusted automation, batch jobs, throwaway sandboxes. |

The user picks a tier per turn; nothing is persisted server-side. The frontend
remembers the choice in `localStorage` so reloads stay consistent.

## Tool classification

Every built-in tool declares a `tool_class` consumed by the `read-only` tier:

| Class | Tools | Read-only behaviour |
|-------|-------|---------------------|
| `read` | `read`, `grep`, `glob`, `skill_view`, `update_working_memory` | Execute freely |
| `memory-write-safe` | `memorize` | Execute freely (the memory subsystem has its own guards) |
| `write` | `bash`, `write`, `edit`, `forget` | Auto-denied |

When `read-only` denies a write-class tool, the agent receives:

```
Tool '<name>' is not available in read-only mode. (Mode can be changed in the input footer.)
```

The wording is intentionally neutral — the agent will not nudge the user to
relax safety; the parenthetical preserves discoverability for the human.

## Configuration

`pyclaw.json` (or env-var overrides) controls per-channel behaviour:

```jsonc
{
  "channels": {
    "web": {
      "enabled": true,
      "defaultPermissionTier": "approval",      // "read-only" | "approval" | "yolo"
      "toolApprovalTimeoutSeconds": 60,         // auto-deny after N seconds
      "toolsRequiringApproval": ["bash", "write", "edit"]
    },
    "feishu": {
      "enabled": true,
      "defaultPermissionTier": "approval",
      "toolApprovalTimeoutSeconds": 60,
      "toolsRequiringApproval": ["bash", "write", "edit"]
    }
  }
}
```

Both channels share the same defaults. You can list `forget` in
`toolsRequiringApproval` if you also want to gate destructive memory ops.
Setting `toolsRequiringApproval` to `[]` while keeping `approval` tier is
equivalent to `yolo` for the gated tools.

## How each channel surfaces approval

### Web channel

- The frontend WebSocket sends `chat.send` with an explicit `tier` field; the
  default ships from `WebSettings.default_permission_tier` via `/api/web/settings`.
- When a gated tool fires, the server emits `tool.approve_request` and the
  frontend opens a modal showing the tool name, arguments, reason, and a tier
  badge so the user sees why the prompt appeared.
- The user clicks Approve / Reject; the runner unblocks and either executes
  the tool or skips with a denial error.
- Per-turn tier override: switching tier mid-session takes effect on the
  next message; in-flight gated calls keep their original tier.

### Feishu (Lark) channel

- A CardKit interactive card is posted with two buttons (✅ Approve / ❌ Deny)
  and a 60-second countdown that updates every 5 seconds.
- The card is `schema: "2.0"` with `update_multi: true` so PATCH updates
  work for the lifetime of the approval.
- **Originator-only authorization**: the open_id of the user who triggered
  the agent is embedded in each button's `value`. Only that user's clicks
  resolve the decision; other group members see a toast (`Only the
  originator can approve/deny this action.`) and the card stays unchanged.
- On decision or timeout, the card is patched to a terminal state
  (`✅ Approved by ou_xxx at HH:MM:SS` / `❌ Denied` / `⌛ Timed Out`) and
  the buttons disappear.
- For this to work the Feishu app must subscribe to the
  `card.action.trigger` callback (Developer Console → Events & Callbacks).

### Per-channel summary

| Capability | Web | Feishu |
|------------|-----|--------|
| Three-tier control | ✅ | ✅ |
| Per-turn tier override | ✅ via `chat.send.tier` | Inherits channel default |
| User-driven approval | ✅ Modal | ✅ CardKit interactive card |
| Originator-only authz | N/A (single-user WS) | ✅ |
| Countdown timer | Implicit (60s) | Visible (5s patch interval) |
| Audit log | ✅ structured JSON | ✅ structured JSON |

## Audit log

Every approval decision (auto or interactive) emits one INFO-level JSON line
on the `pyclaw.audit.tool_approval` logger:

```json
{
  "event": "tool_approval_decision",
  "ts": "2026-05-16T10:30:45Z",
  "conv_id": "conv_abc",
  "session_id": "web:alice:c1",
  "channel": "web",
  "tool_name": "bash",
  "tool_call_id": "call_42",
  "tier": "approval",
  "decision": "approve",
  "decided_by": "user_alice",
  "decided_at": "2026-05-16T10:30:50Z",
  "elapsed_ms": 5333,
  "user_visible_name": "@alice"
}
```

`decided_by` is one of:

| Value | Meaning |
|-------|---------|
| `auto:not-gated` | Tool not in `tools_requiring_approval`; auto-approved |
| `auto:read-only` | Write-class tool denied because tier is read-only |
| `auto:yolo` | Implicit pass-through (rarely emitted; runner skips logging on yolo) |
| `auto:timeout` | Approval timed out without a user response |
| `auto:post-failed` | Channel could not post the approval prompt (e.g. CardKit API failure) |
| `<user-id>` | Web `user_id` or Feishu `open_id` of the approver |

The Sprint 1 implementation persists audit lines only to the logger sink
(stdout / file / journald per deployment). For long-term retention, wire your
log aggregation (Loki / ELK / Cloud logs). A persistent store
(Redis sorted set / SQLite) is tracked as Sprint 1.1 follow-up TA2.

## Tier-change bookmarks

In addition to per-decision lines, the audit log emits a single line each
time a session's permission tier changes (mid-conversation tier toggle):

```json
{
  "event": "permission_tier_changed",
  "ts": "2026-05-16T10:30:45Z",
  "session_id": "web:alice:c1",
  "channel": "web",
  "from_tier": "approval",
  "to_tier": "yolo",
  "user_id": "alice"
}
```

Use these to answer questions like "when did this session switch into
yolo?" with a single grep, without scanning every tool decision line.

## Log level and operational guidance

Audit lines are emitted at **INFO level** on the `pyclaw.audit.tool_approval`
logger. This matches the convention used by sudo, AWS CloudTrail, Spring Boot
Actuator, and Vault: audit is a privileged-action record that must reach the
sink in production, so it is never relegated to DEBUG.

If you want to silence noisy third-party libraries (e.g. lark-oapi) in
production but keep audit lines, configure the audit logger explicitly so
its level is independent of the root logger:

```python
import logging
audit_logger = logging.getLogger("pyclaw.audit.tool_approval")
audit_logger.setLevel(logging.INFO)
audit_logger.propagate = True  # let your root handler receive it
```

For ELK / Loki / Splunk pipelines: filter on
`event=tool_approval_decision OR event=permission_tier_changed` to isolate
the audit stream.

## Relationship to D26 isolation

Tool approval is **per-session**. Two users on the same deployment have
independent conversation pipelines, independent approval queues, and
independent audit streams. The originator-only check on Feishu prevents
cross-user approval within a group chat, but cross-tenant isolation
(multi-team SaaS) still requires the upgrades described in
[D26: User Isolation Model](./architecture-decisions.md#d26-user-isolation-model--personal-assistant-not-multi-tenant-saas).

## Known limitations (tracked in KNOWN-ISSUES.md)

- **TA1** per-tool glob (e.g. `bash:rm -rf *: deny`) — Sprint 1.1 follow-up
- **TA2** persistent audit log — Sprint 1.1 if needed
- **TA3** per-user permission profile — pairs with multi-tenancy
- **TA6** CardKit countdown rate-limit (50 concurrent ceiling) — monitored

## Recovering from a stuck approval

If the Feishu callback subscription is mis-configured the card will not
respond to clicks. Either:

1. Wait 60 s for the timeout to auto-deny.
2. Restart PyClaw — the approval registry is in-memory; on restart the
   pending decision is cleared and the agent run terminates with the
   timeout deny.

Symptoms in the Feishu Developer Console: no `card.action.trigger` events.
Fix: subscribe to that callback under Events & Callbacks → Subscribed
Callbacks. WebSocket-mode requires the long-connection setting enabled.
