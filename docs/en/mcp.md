# MCP Servers (Model Context Protocol)

PyClaw supports the Anthropic [Model Context Protocol](https://modelcontextprotocol.io)
ecosystem. Configuring a few lines in `pyclaw.json` adds tools from
`@modelcontextprotocol/server-filesystem`, `@modelcontextprotocol/server-github`,
and any other MCP-compliant server to your agent — alongside builtin tools,
gated by the same Sprint 1 permission tier system.

## Quickstart (filesystem server, 30 seconds)

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/me/projects"]
      }
    }
  }
}
```

Restart PyClaw. After ~5 seconds the agent has access to ~11 filesystem tools
(`filesystem:read_file`, `filesystem:write_file`, `filesystem:list_directory`, …).
Run `/mcp list` in any channel to confirm.

## Configuration reference

```jsonc
{
  "mcp": {
    "enabled": false,
    "servers": {
      "<server-name>": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "{env:GITHUB_TOKEN}"},
        "transport": "stdio",
        "enabled": true,
        "trust_annotations": true,
        "forced_tool_class": null,
        "forced_tier": null,
        "connect_timeout_seconds": 30.0,
        "call_timeout_seconds": 60.0
      }
    }
  }
}
```

| Field | Type | Default | Purpose |
|---|---|---|---|
| `command` | string | required | Executable to spawn (`npx`, `uvx`, full path). |
| `args` | string[] | `[]` | Arguments passed to `command`. |
| `env` | object | `{}` | Env vars for the spawned process. Values may use `{env:VAR}` placeholders (resolved from the PyClaw process environment). |
| `transport` | `"stdio"` | `"stdio"` | Transport. Sprint 2 only supports `stdio`. SSE / streamable-http are tracked for Sprint 2.1. |
| `enabled` | bool | `true` | Per-server kill switch without removing config. |
| `trust_annotations` | bool | `true` | When `true`, derive `tool_class` from the MCP server's `ToolAnnotations.readOnlyHint`. When `false`, every tool from this server is treated as `tool_class="write"` regardless of what the server claims. |
| `forced_tool_class` | `"read"` \| `"write"` \| null | null | Operator override of the derived `tool_class`. Useful to declassify or upgrade a server's tools regardless of its annotations. |
| `forced_tier` | `"read-only"` \| `"approval"` \| `"yolo"` \| null | null | Per-server permission tier. **De-escalation only** (see below). |
| `connect_timeout_seconds` | float | 30.0 | Maximum seconds to wait for `connect_to_server` during startup. |
| `call_timeout_seconds` | float | 60.0 | Maximum seconds to wait for one tool call. |

> **Server config keys** (the dict keys under `servers`) MUST NOT contain `:`
> or `__`. Both are reserved for the canonical `{server}:{tool}` namespace and
> the LLM-API `__` rewrite. Pydantic validation rejects offending keys with
> a clear error.

## Secrets via `{env:VAR}` placeholders

Embedding secrets in `pyclaw.json` is unsafe (gets committed, gets leaked). The
`env` field supports `{env:VAR_NAME}` whole-string placeholders that resolve
from the PyClaw process environment at startup:

```jsonc
{
  "mcp": {
    "enabled": true,
    "servers": {
      "github": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-github"],
        "env": {"GITHUB_TOKEN": "{env:GITHUB_TOKEN}"}
      }
    }
  }
}
```

Then run PyClaw with the var set:

```bash
GITHUB_TOKEN=ghp_xxx ./scripts/start.sh
```

The placeholder regex is `^\{env:([A-Z_][A-Z0-9_]*)\}$` (uppercase / underscore /
digit, leading non-digit). It matches the **whole** string only — partial
matches like `"prefix-{env:VAR}"` are passed through verbatim by design (no
"magical" partial substitution). Lowercase env var names also pass through
verbatim.

Missing env vars cause that server to fail with a clear message:
`MCP server 'github' failed: env var referenced in 'GITHUB_TOKEN' is not set`.
Other servers continue connecting; PyClaw stays up.

## Permission tier integration

Every MCP tool gets a `tool_class` derived at registration time:

```
forced_tool_class (operator)            # wins if set
  → trust_annotations AND readOnlyHint  # → "read"
  → "write"                             # safe default
```

The tier evaluator runs **per call**:

| Per-turn tier | `forced_tier` | Outcome |
|---|---|---|
| any | unset | use per-turn |
| `yolo` | `"approval"` | use `"approval"` (de-escalation: `RANK[approval]=1 > RANK[yolo]=0`) |
| `yolo` | `"read-only"` | use `"read-only"` |
| `approval` | `"read-only"` | use `"read-only"` |
| `approval` | `"yolo"` | **stays `"approval"`** — `forced_tier` cannot escalate |
| `read-only` | any | stays `"read-only"` — user's strictest choice wins |

> **`forced_tier` is de-escalation only.** Setting `forced_tier="yolo"` on an
> untrusted server CANNOT bypass a user who chose `approval`. This prevents a
> server config bug from silently weakening the user's permission gate.
> Audit logs record `tier_source="forced-by-server-config"` only when the
> forced tier was strictly more restrictive (and so actually engaged).

When a call is partitioned into the `approval` bucket, the runner passes the
**canonical** name (`github:search_issues`) to the approval hook, not the
LLM-form (`github__search_issues`). Operators who configure
`tools_requiring_approval: ["github:search_issues"]` see the match they expect.
`forced_tier="approval"` calls bypass the `tools_requiring_approval` allow-list
entirely — every tool from the forced server is gated regardless.

## `/mcp` slash command

| Subcommand | Purpose |
|---|---|
| `/mcp list` | Per-server table: name, status (connected / failed / disabled / pending), tool count, last_connect_at. Header shows aggregate counts and `is_ready()`. |
| `/mcp restart <name>` | Atomically swap a server's adapters. Per-server `asyncio.Lock` serializes against any concurrent supervisor reconnect. |
| `/mcp logs <name>` | Last ~3000 chars of the server's stderr ring buffer with **secret redaction** (any value present in the server's resolved `env` dict is replaced with `<REDACTED>`). |

`/mcp list` works mid-streaming. Available on Web and Feishu channels.

## Non-blocking startup

PyClaw spawns MCP server connections in a **background supervisor task** —
the FastAPI lifespan does NOT block on per-server connectivity. The
`/health` endpoint returns 200 OK immediately with an advisory `mcp` field:

```json
{
  "status": "ok",
  "mcp": {
    "ready": false,
    "n_connected": 1,
    "n_failed": 0,
    "n_pending": 2,
    "n_disabled": 0,
    "total_tools": 11
  }
}
```

K8s readiness probes should NOT use MCP connectivity as a gate — PyClaw stays
alive even if every MCP server fails. See `design.md` D7 for the full
rationale (6 deployment scenarios + 6 trade-offs).

A consequence: the **first chat request** after PyClaw boots may see a
*partially populated* tool set (e.g., 8 builtin + 11 from the fast server,
while the slower server is still connecting). Subsequent iterations
automatically pick up newly-connected tools. To wait deterministically, probe
`/health` until `mcp.n_pending == 0` or run `/mcp list`.

## Failure handling

* **Server crashes mid-call** — the adapter raises `MCPServerDeadError`
  internally; the dispatcher catches it (it does NOT propagate above
  `_dispatch_single`) and schedules `_handle_server_death` non-blockingly via
  `task_manager.spawn`. The server transitions to `failed`, all its adapters
  are unregistered. The current tool call returns an error `ToolResult`
  (`MCP server 'github' is unavailable. Removed from this conversation. Use
  /mcp restart github to retry.`) and **sibling parallel calls continue
  unaffected**.

* **`/mcp restart` failure** — the spec's safer-by-default semantic: if
  reconnect fails, the OLD adapters are also removed and the server is
  marked `failed`. **Plan restarts of production-critical servers during
  low-traffic windows** — a failed restart leaves the server worse off
  than before.

* **Auto-restart** — Sprint 2 has none. Operators must `/mcp restart` after
  fixing the root cause (credentials, network, etc.). Auto-restart with
  backoff is a Sprint 2.1 candidate.

## Security notes

* **`ToolAnnotations` is a hint, not a guarantee.** A server claiming
  `readOnlyHint=true` could still mutate state. Set `trust_annotations: false`
  for any server you don't fully audit, OR set `forced_tool_class: "write"`
  to force every tool through the write-class gate.

* **`{env:VAR}` substitution applies ONLY to the `env` field.** It does
  NOT substitute into `command` / `args` — secrets leaked to argv would be
  visible in `ps` output anyway, so this is by design. If you need a
  templated command, write a wrapper script.

* **`/mcp logs` redaction is best-effort.** It substring-replaces any value
  present in the server's resolved `env` dict with `<REDACTED>`. It does NOT
  catch derived hashes, base64 fragments, or secrets the server prints with
  modifications. Don't ship `/mcp logs` output to public issue trackers
  without a manual review.

* **Audit log fields.** Every MCP-tool approval decision logs `tier_source`
  ("per-turn" / "channel-default" / "forced-by-server-config"). Forced
  decisions also include `forced_server`. Consume the audit log for
  post-hoc forensics if you suspect a misbehaving server.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/mcp list` shows server `failed`, reason `connect timeout (30s)` | Server's first run is downloading npm package | Wait, then `/mcp restart <name>`. Or warm npm cache: `npx @modelcontextprotocol/server-foo --help`. |
| `/mcp list` shows `failed`, reason `env var referenced in 'X' is not set` | Missing env var | Set in PyClaw's process env (systemd `EnvironmentFile=`, docker `--env-file`, k8s Secret) |
| `/mcp list` shows `failed`, reason starts with `tool name collision` | Two servers expose a tool with the same remote name | Disable one of them (`enabled: false`) or rename via `forced_tool_class` (planned for Sprint 2.1) |
| Agent can't find an MCP tool the LLM tried to call | Tool name collision with an existing builtin, or the server's tool list includes `:` in remote names (those are skipped) | Run `/mcp logs <name>` and grep for "rejected: tool name contains ':'" |
| `/mcp restart` says success but `/mcp list` still shows failed | Race window: the new connection actually re-failed. Run `/mcp logs <name>`. | Diagnose root cause; the spec's safer semantic removes adapters on a failed restart |
| Web UI approval modal shows ugly `__`-form name (`filesystem__read_file`) instead of `:`-form | Bug: runner should pass canonical name to hook | Should not happen as of v4 — file an issue |
| `/health` returns `mcp.n_pending=N` for too long | Slow npm registry / network blip | Wait, or `/mcp restart`. PyClaw stays available throughout. |

See also: [permissions](permissions.md), [configuration](configuration.md),
[deployment](deployment.md).
