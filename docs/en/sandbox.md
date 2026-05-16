# Sandbox

Sprint 3 ships an opt-in process-isolation layer for `BashTool` and per-server
MCP subprocesses, backed by Anthropic's
[`@anthropic-ai/sandbox-runtime`](https://www.npmjs.com/package/@anthropic-ai/sandbox-runtime)
(`srt` 1.0.0+). The default policy is `none` so existing deployments behave
identically to Sprint 2.0.1.

## TL;DR — Three Backends

| `sandbox.policy` | Behavior | When to use |
|---|---|---|
| `none` (default) | No isolation; parent env inherited; byte-identical to Sprint 2.0.1 | Local dev, trusted single-user |
| `srt` | Wraps `BashTool` and (opt-in) MCP servers with `srt` (macOS Seatbelt + Linux bwrap+seccomp) | Multi-user, internet-facing, compliance |

## Quickstart

1. Install the runtime (any platform, requires Node.js):

   ```bash
   npm install -g @anthropic-ai/sandbox-runtime
   srt --version   # → 1.0.0
   ```

2. Enable in `pyclaw.json`:

   ```json
   {
     "sandbox": {
       "policy": "srt",
       "productionRequireSandbox": false,
       "defaultFilesystem": {
         "allowWrite": ["~/.pyclaw/workspaces"],
         "denyRead": ["~/.ssh", "~/.aws", "~/.config"],
         "denyWrite": []
       },
       "defaultNetwork": {
         "allowedDomains": ["api.anthropic.com"],
         "deniedDomains": ["169.254.169.254"]
       },
       "defaultEnvAllowlist": []
     }
   }
   ```

3. Restart PyClaw and verify:

   ```bash
   curl -s localhost:8000/health | jq .sandbox
   # { "ready": true, "backend": "srt", "srt_version": "1.0.0", "warning": null }
   ```

4. As an admin user: `/admin sandbox check` prints state + per-MCP-server status.

## Required `srt` Schema (1.0.0+)

PyClaw generates `srt-settings.json` per call with all required fields. Note:

- `network.allowedDomains` MUST list specific domains. The pattern `["*"]` is
  rejected by `srt`.
- `network.deniedDomains` MUST be present (PyClaw defaults
  `["169.254.169.254"]` for cloud-metadata IMDS protection).
- `filesystem.{allowWrite, denyRead, denyWrite}` MUST all be present (use `[]`
  for empty).

## Per-User Overrides

`UserProfile.sandbox_overrides` (JSON-fallback or set via `/admin user set`)
extends — never replaces — the deployment defaults:

```json
{
  "channels": {
    "web": {
      "users": [
        {
          "id": "alice",
          "password": "...",
          "role": "admin",
          "sandbox_overrides": {
            "filesystem": { "allowWrite": ["/tmp/alice-projects"] },
            "network": { "allowedDomains": ["registry.npmjs.org"] }
          }
        }
      ]
    }
  }
}
```

## Hardcoded Env Deny Floor (4-slot review F10)

`build_clean_env(allowlist=...)` always strips a hardcoded set of credential
env vars **regardless** of the configured allowlist. This guards against an
admin (or compromised admin token) accidentally exposing secrets via
`env_allowlist=["*"]`.

Always-stripped names include:

- `ANTHROPIC_API_KEY`, `ANTHROPIC_*`, `PYCLAW_*`, `LITELLM_*` (PyClaw / model
  credentials)
- `OPENAI_API_KEY`
- `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN` (AWS;
  `AWS_REGION` is allowed if explicitly listed)
- `SSH_AUTH_SOCK`, `SSH_AGENT_PID`
- `GITHUB_TOKEN`, `GH_TOKEN`
- `KUBECONFIG`, `KUBE_TOKEN`

`env_allowlist=["AWS_*"]` is **rejected at config-load time** with a clear
error. Use specific names like `AWS_REGION` instead.

## MCP Server Sandbox

Per-server `sandbox.enabled` defaults are conditional on the command:

| Command basename | Default | Reason |
|---|---|---|
| `npx`, `uvx` | `false` | Auto-exempt — sandbox blocks registry probe (4-slot review F1) |
| anything else | `true` | Local binary path → isolation by default (Sprint 3 D6) |

Operators can always force the value:

```json
{
  "mcp": {
    "enabled": true,
    "servers": {
      "fs": {
        "command": "/usr/local/bin/mcp-server-fs",
        "args": ["/tmp"],
        "sandbox": { "enabled": true }
      },
      "github": {
        "command": "npx",
        "args": ["-y", "@github/mcp-server"],
        "sandbox": { "enabled": false }
      }
    }
  }
}
```

When `sandbox.enabled=true` but `srt` is missing, the server is marked
`failed` in `/health.mcp` (and `/admin sandbox check`) without blocking the
PyClaw main process. Other servers stay reachable.

## Sprint 2 → Sprint 3 Migration

If you upgraded from Sprint 2.0.1 with no `sandbox` block in `pyclaw.json`:

1. **Nothing breaks.** Default `sandbox.policy="none"` keeps Sprint 2.0.1
   behavior byte-identical.
2. **Per-server MCP defaults** changed:
   - `command="npx" / "uvx"` configurations: still no sandbox (auto-exempt).
     PyClaw startup logs INFO with the rationale.
   - `command="<local-binary-path>"` configurations: PyClaw startup logs a
     WARNING because `sandbox.enabled` defaulted to `true`. Either install
     `srt` or set `sandbox: { enabled: false }` explicitly to opt out.
3. **To upgrade**: install `srt`, set `sandbox.policy="srt"`, restart.
   `/admin sandbox check` will guide remaining tweaks.

## Emergency Override

If `srt` is mandatory (`productionRequireSandbox=true`) but ops needs to start
the server without it (e.g. npm install failed mid-rollout):

```bash
PYCLAW_SANDBOX_OVERRIDE=disable .venv/bin/uvicorn pyclaw.app:app
```

PyClaw logs a CRITICAL warning and `/health.sandbox.warning` carries the
override notice for visibility. **Do not** leave this in production startup
scripts.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/health.sandbox.backend = "none"` despite `policy="srt"` | `srt` missing on PATH | Install via `npm install -g @anthropic-ai/sandbox-runtime` |
| MCP server status `failed: srt-config: …` | Generated `srt-settings.json` rejected | Inspect log; usually `network.allowedDomains` includes `"*"` or required field missing |
| `npx` MCP server fails to fetch package | npx + sandbox + missing registry domain | Either set `sandbox.enabled=false` or add `registry.npmjs.org` to `network.allowedDomains` |
| `bash` exits with `permission denied` reading `~/.aws/credentials` | Working as designed — sandbox `denyRead` blocks credential paths | Add specific path to `sandbox_overrides.filesystem.allowRead` if legitimate |

## Audit Trail

Every tool decision under sandbox emits an audit line including
`sandbox_backend`. Grep an example with:

```bash
journalctl -u pyclaw | grep tool_approval_decision | jq 'select(.sandbox_backend == "srt")'
```
