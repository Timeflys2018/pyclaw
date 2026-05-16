# Multi-User Deployment

Sprint 3 upgrades PyClaw's isolation model from "personal/trusted-team only"
(D26) to **configurable multi-user with role + sandbox isolation**. This guide
covers the operator workflow for running PyClaw as a shared service.

## Concepts

| Concept | What it controls | Storage |
|---|---|---|
| **Role** (`admin` / `member`) | Whether the user can run `/admin user set/list/show` and `/admin sandbox check` | Redis-live (`pyclaw:userprofile:{channel}:{user_id}`) + JSON fallback |
| **`tier_default`** | Per-user default of the 4-layer tier resolution chain (per-message > sessionKey > **user** > deployment) | Same as above |
| **`tools_requiring_approval`** | REPLACES (not unions) the channel default list when set; `[]` means "gate nothing" | Same as above |
| **`env_allowlist`** | Extra env vars allowed into BashTool subprocess (subject to hardcoded deny floor) | Same as above |
| **`sandbox_overrides`** | Extends deployment sandbox defaults for filesystem/network | Same as above |

## Channel Isolation (Sprint 3 D2)

Web `alice` and Feishu `ou_alice` are **independent profiles**. They live
under separate Redis keys and JSON arrays:

```
pyclaw:userprofile:web:alice
pyclaw:userprofile:feishu:ou_a1b2c3
```

Cross-channel mapping (e.g. "this Feishu user IS the Web alice") is on the
Sprint 3.x backlog.

## Configure Users via JSON (Operator)

```json
{
  "channels": {
    "web": {
      "users": [
        {
          "id": "alice",
          "password": "...",
          "role": "admin",
          "tier_default": "yolo"
        },
        {
          "id": "bob",
          "password": "...",
          "role": "member",
          "tier_default": "read-only",
          "tools_requiring_approval": ["bash"]
        }
      ]
    }
  }
}
```

Restart required for JSON changes.

## Configure Users at Runtime (Admin Slash Command)

As an admin, in any chat:

```
/admin user list
/admin user show bob
/admin user set bob tier=read-only role=member
```

Redis writes (TTL 30 days, sliding window) take effect on next message — no
restart. Redis values **shadow** JSON for the same `user_id` (Redis wins on
overlap).

### Last-Admin Protection (4-slot review F4)

If alice is the **only** admin, `/admin user set alice role=member` is
refused with `❌ Cannot demote the last admin. Promote another user to admin
first.` Promote a co-admin first, then demote.

## Tier Resolution (Sprint 3 4-Layer)

For every tool call, the runner resolves the effective tier in this order
(first non-None wins):

1. **Per-message override** — Web SPA tier picker / Feishu `/tier <tier> --once`
2. **Per-sessionKey override** — `/tier <tier>` (persists for the sessionKey)
3. **Per-user `tier_default`** ← Sprint 3 NEW
4. **Channel deployment default** — `settings.channels.{web,feishu}.default_permission_tier`

Per-server `forced_tier` (Sprint 2) layers on top: it can only **escalate
restrictions**, never relax them.

## Audit Trail (Sprint 3 Enhancement)

Every tool decision audit line now carries:

```json
{
  "event": "tool_approval_decision",
  "user_id": "alice",
  "role": "member",
  "sandbox_backend": "srt",
  "tier_source": "user-default",
  ...
}
```

Operators can reconstruct "what did alice do under sandbox last week" with a
single `jq` query.

## Recommended Production Setup

1. Set `sandbox.policy="srt"` + install `srt` (see [sandbox.md](./sandbox.md))
2. Create a small `users[]` JSON with at least one `role=admin` user
3. Set `default_permission_tier="approval"` on Web and Feishu channels
4. Configure Web users to use `tier_default="read-only"` if interactive editing
   is rare
5. Make sure Redis is enabled (`storage.session_backend=redis`) — without it,
   `/admin user set` cannot persist runtime profile updates

## See Also

- [Permissions and Approval Tiers](./permissions.md) — full tier semantics
- [Sandbox](./sandbox.md) — isolation backend setup
- [Architecture Decision D26](./architecture-decisions.md#d26-user-isolation-model)
