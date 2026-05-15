# Configuration Reference

PyClaw is configured via a single JSON file (`pyclaw.json`) that controls Redis,
model providers, the memory system, agent behavior, channels, skills,
self-evolution, and the affinity gateway. This document is **scenario-driven**:
five common setups with minimal working JSON, followed by a complete field
reference.

## Table of Contents

- [Configuration File Discovery](#configuration-file-discovery)
- [Scenario 1: Local development, zero deps](#scenario-1-local-development-zero-deps)
- [Scenario 2: Production, single instance (web channel)](#scenario-2-production-single-instance-web-channel)
- [Scenario 3: Production, active-active multi-instance](#scenario-3-production-active-active-multi-instance)
- [Scenario 4: Feishu (Lark) bot](#scenario-4-feishu-lark-bot)
- [Scenario 5: Memory + self-evolution](#scenario-5-memory--self-evolution)
- [Complete field reference](#complete-field-reference)
- [Environment-variable overrides](#environment-variable-overrides)

---

## Configuration File Discovery

On startup PyClaw searches for the config file in this order:

1. `pyclaw.json` (current working directory)
2. `configs/pyclaw.json` (project directory)
3. `~/.openclaw/pyclaw.json` (user directory)

The first existing file is loaded. If none exist, defaults apply (web and Feishu
both disabled, single-process in-memory mode).

The complete template lives at [`configs/pyclaw.example.json`](../../configs/pyclaw.example.json)
(167 lines, every optional field).

---

## Scenario 1: Local development, zero deps

Smallest viable config — no Redis, no external DB, just `./scripts/start.sh`:

```json
{
  "server": { "host": "127.0.0.1", "port": 8000 },
  "storage": { "session_backend": "memory", "lock_backend": "file" },
  "agent": {
    "default_model": "anthropic/claude-sonnet-4-20250514",
    "providers": {
      "anthropic": {
        "apiKey": "sk-ant-...",
        "models": {
          "anthropic/claude-sonnet-4-20250514": {
            "modalities": { "input": ["text", "image"], "output": ["text"] }
          }
        }
      }
    }
  },
  "channels": {
    "web": {
      "enabled": true,
      "jwtSecret": "dev-only-jwt-secret",
      "users": [{ "id": "admin", "password": "changeme" }]
    }
  }
}
```

**Highlights**:
- `storage.session_backend: "memory"` — in-process dict; sessions lost on restart
- `storage.lock_backend: "file"` — file locks under `~/.pyclaw/locks/` (fine for single-process)
- `agent.providers.anthropic.models.<id>.modalities` — **declares input/output
  capabilities** for the model. `["text", "image"]` means images are accepted;
  the agent runner validates this and returns `vision_not_support` for models
  without image input
- `channels.web.users` — plaintext credentials, **dev only**

---

## Scenario 2: Production, single instance (web channel)

One PyClaw container plus one Redis serving tens-to-hundreds of web users:

```json
{
  "server": { "host": "0.0.0.0", "port": 8000 },
  "redis": {
    "host": "redis.internal",
    "port": 6379,
    "password": "${REDIS_PASSWORD}",
    "keyPrefix": "pyclaw:"
  },
  "storage": {
    "session_backend": "redis",
    "lock_backend": "redis"
  },
  "agent": {
    "default_model": "anthropic/claude-sonnet-4-20250514",
    "max_iterations": 50,
    "timeouts": {
      "run_seconds": 300,
      "idle_seconds": 60,
      "tool_seconds": 120
    },
    "providers": {
      "anthropic": {
        "apiKey": "${ANTHROPIC_API_KEY}",
        "models": {
          "anthropic/claude-sonnet-4-20250514": {
            "modalities": { "input": ["text", "image"], "output": ["text"] }
          }
        }
      }
    }
  },
  "channels": {
    "web": {
      "enabled": true,
      "jwtSecret": "${JWT_SECRET}",
      "adminToken": "${ADMIN_TOKEN}",
      "heartbeatInterval": 30,
      "pongTimeout": 10,
      "maxConnectionsPerUser": 3,
      "defaultPermissionTier": "approval",
      "toolApprovalTimeoutSeconds": 60,
      "toolsRequiringApproval": ["bash", "write", "edit"],
      "corsOrigins": ["https://chat.example.com"],
      "users": [{ "id": "alice", "password": "$ARGON2_HASH..." }]
    }
  }
}
```

**Highlights**:
- `storage.session_backend: "redis"` — sessions persist across restarts and can
  be shared across instances (see Scenario 3)
- `storage.lock_backend: "redis"` — distributed lock (SET NX PX + Lua CAS)
  prevents two workers from racing on the same session
- `redis.keyPrefix` — namespace prefix when multiple PyClaw deployments share
  the same Redis
- `${ENV}` is conventional notation; PyClaw does **not** auto-expand env vars
  inside JSON. Two practical approaches:
  1. Render the template with `envsubst` before launch
  2. Use environment variable overrides directly (see [below](#environment-variable-overrides))
- `web.maxConnectionsPerUser: 3` — caps concurrent WS connections per account
- `web.defaultPermissionTier` — `"read-only" | "approval" | "yolo"` (default
  `"approval"`). Read-only auto-denies write-class tools; approval gates the
  list below; yolo skips the gate. See [permissions guide](./permissions.md).
- `web.toolApprovalTimeoutSeconds` — auto-deny after this many seconds without
  a user response (default `60`)
- `web.toolsRequiringApproval` — listed tools prompt the user before execution
  (default: `["bash", "write", "edit"]`); write-class tools are gated by default
- `web.corsOrigins` — strict CORS; list real frontend origins

---

## Scenario 3: Production, active-active multi-instance

Three PyClaw workers + one Redis + one nginx (`ip_hash` plus the Session
Affinity Gateway), horizontally scaled:

```json
{
  "server": { "host": "0.0.0.0", "port": 8000 },
  "redis": {
    "host": "redis",
    "port": 6379,
    "password": "${REDIS_PASSWORD}",
    "keyPrefix": "pyclaw:"
  },
  "storage": {
    "session_backend": "redis",
    "lock_backend": "redis"
  },
  "affinity": {
    "enabled": true,
    "ttl_seconds": 300,
    "heartbeat_interval": 30,
    "stale_threshold": 90,
    "renewal_interval": 60
  },
  "agent": { "default_model": "...", "providers": { "...": "..." } },
  "channels": {
    "web": { "enabled": true, "jwtSecret": "${JWT_SECRET}", "adminToken": "${ADMIN_TOKEN}", "users": [...] }
  }
}
```

**Highlights**:
- `affinity.enabled: true` — turns on the Session Affinity Gateway. Each worker
  registers itself in Redis (`pyclaw:workers` zset) on startup; the first
  request for a session locks that session to a specific worker
  (`session_key → worker_id`). Even if the load balancer routes a later request
  to a different worker, that worker forwards the message to the owner via
  Redis PubSub.
- `affinity.ttl_seconds: 300` — TTL on the affinity record; workers refresh it
  every `renewal_interval` seconds via heartbeat.
- `affinity.stale_threshold: 90` — a worker missing heartbeats for this long is
  declared dead; the gateway's PUBLISH-subscriber-count detection triggers
  `force_claim` failover.
- All three workers use the **same** `pyclaw.json` (read-only volume mount); no
  worker-specific config.
- nginx must be configured with `ip_hash` (see [`deploy/nginx.conf`](../../deploy/nginx.conf))
  — that's a perf optimization (less cross-worker forwarding); the affinity
  gateway is the correctness guarantee (still works if nginx misroutes).

See the [deployment guide](./deployment.md#3-production-active-active) for
launch instructions.

---

## Scenario 4: Feishu (Lark) bot

The Feishu channel uses long-lived WebSocket connections (Feishu cluster mode)
and does not require a public ingress. The web channel can run in parallel:

```json
{
  "redis": { "host": "redis", "port": 6379 },
  "storage": { "session_backend": "redis", "lock_backend": "redis" },
  "agent": { "default_model": "...", "providers": { "...": "..." } },
  "channels": {
    "feishu": {
      "enabled": true,
      "appId": "cli_a1b2c3d4e5f6",
      "appSecret": "${FEISHU_APP_SECRET}",
      "sessionScope": "chat",
      "groupContext": "recent",
      "groupContextSize": 20,
      "idleMinutes": 0,
      "streaming": {
        "printFrequencyMs": 50,
        "printStep": 2,
        "printStrategy": "fast",
        "throttleMs": 100
      }
    }
  }
}
```

**Highlights**:
- `appId` / `appSecret` — credentials from your Feishu Open Platform app
  (https://open.feishu.cn)
- `sessionScope: "chat"` — one session per chat (DM or group); alternative
  `"user"` shares one session per user across all chats
- `groupContext: "recent"` — in group chats, inject the user's last N messages
  as context; alternative `"thread"` (only thread-reply chain) or `""` (none)
- `groupContextSize: 20` — N for the `recent` mode
- `idleMinutes: 0` — `0` disables idle reset; `30` would start a fresh session
  if no message arrives for 30 minutes
- `streaming` — Feishu CardKit streaming card "typing" parameters; lower
  `throttleMs` looks more real-time but consumes more API quota

---

## Scenario 5: Memory + self-evolution

PyClaw's headline feature — four-layer memory (L1 Redis index + L2/L3 SQLite
FTS5 + L4 sqlite-vec archives), plus automatic SOP extraction with a Curator
lifecycle:

```json
{
  "redis": { "host": "redis" },
  "storage": { "session_backend": "redis", "memory_backend": "sqlite" },
  "memory": {
    "base_dir": "~/.pyclaw/memory",
    "l1_max_entries": 30,
    "l1_max_chars": 3000,
    "l1_ttl_seconds": 2592000,
    "search_l2_quota": 3,
    "search_l3_quota": 2,
    "archive_max_results": 5,
    "archive_min_similarity": 0.5,
    "archive_enabled": true
  },
  "embedding": {
    "model": "openai/text-embedding-3-small",
    "apiKey": "${OPENAI_API_KEY}",
    "dimensions": 1536
  },
  "evolution": {
    "enabled": true,
    "extraction_model": "anthropic/claude-haiku-4",
    "minToolCallsForExtraction": 2,
    "dedupOverlapThreshold": 0.6,
    "maxSopsPerExtraction": 5,
    "curator": {
      "enabled": true,
      "intervalSeconds": 604800,
      "staleAfterDays": 30,
      "archiveAfterDays": 90,
      "graduationEnabled": true,
      "promotionMinUseCount": 5,
      "promotionMinDays": 7
    }
  },
  "agent": { "default_model": "...", "providers": { "...": "..." } }
}
```

**Highlights**:
- `memory.base_dir` — where SQLite files live. In multi-instance deployments,
  this **must** be a shared volume (NFS / EFS); otherwise workers see
  divergent memory.
- `memory.search_l2_quota` / `search_l3_quota` — caps on facts/procedures
  injected per prompt; raising them improves recall at the cost of tokens.
- `memory.archive_min_similarity: 0.5` — vector-similarity floor for L4
  recall; archives below this are not injected.
- `embedding` — required for L4 archives; if not configured, set
  `archive_enabled: false`.
- `evolution.extraction_model` — dedicated model for SOP extraction; can be a
  cheaper Haiku-class model, no need to match the main agent.
- `evolution.curator.staleAfterDays: 30` / `archiveAfterDays: 90` — the
  Curator background task tags SOPs unused for 30 days as stale and archives
  those unused for 90 days.
- `evolution.curator.graduationEnabled: true` — frequently used SOPs graduate
  to SKILL.md (progressive disclosure).

---

## Complete field reference

Organized by Settings class. Each row lists the field name (JSON alias /
Python snake_case), type, default, and purpose.

### `server`

| Field | Type | Default | Purpose |
|---|---|---|---|
| `host` | str | `"0.0.0.0"` | uvicorn bind address |
| `port` | int | `8000` | uvicorn port |

### `redis`

| Field | Type | Default | Purpose |
|---|---|---|---|
| `host` | str | `"localhost"` | Redis host |
| `port` | int | `6379` | Redis port |
| `password` | str / null | `null` | leave `null` if unset |
| `url` | str | `""` | full URL like `redis://...`; if set, ignores host/port/password |
| `keyPrefix` / `key_prefix` | str | `"pyclaw:"` | prefix for all Redis keys |
| `transcriptRetentionDays` / `transcript_retention_days` | int | `7` | session transcript Redis TTL (days) |

### `storage`

| Field | Default | Allowed values |
|---|---|---|
| `session_backend` | `"memory"` | `"memory"` (single-process dict) / `"redis"` (production) |
| `memory_backend` | `"sqlite"` | `"sqlite"` (only choice today) |
| `lock_backend` | `"file"` | `"file"` (local) / `"redis"` (distributed) |

### `memory`

| Field | Default | Purpose |
|---|---|---|
| `base_dir` | `~/.pyclaw/memory` | SQLite files directory |
| `l1_max_entries` | 30 | L1 Redis working memory cap |
| `l1_max_chars` | 3000 | L1 max chars per entry |
| `l1_ttl_seconds` | 2592000 | L1 TTL (30 days) |
| `search_l2_quota` | 3 | facts injected per prompt |
| `search_l3_quota` | 2 | procedures injected per prompt |
| `search_fts_min_query_chars` | 3 | min chars for FTS query (skip if shorter) |
| `archive_max_results` | 5 | L4 recall cap |
| `archive_min_similarity` | 0.5 | L4 vector-similarity floor |
| `archive_min_results` | 1 | minimum L4 results returned (returned even if below threshold) |
| `archive_enabled` | true | L4 master switch |
| `namingPolicy` / `naming_policy` | `"human"` | `"human"` (readable IDs) or `"hash"` |

### `embedding`

| Field | Default | Purpose |
|---|---|---|
| `model` | `""` | embedding model ID (litellm format, e.g. `openai/text-embedding-3-small`) |
| `apiKey` / `api_key` | `""` | API key |
| `baseURL` / `base_url` | `""` | custom endpoint |
| `dimensions` | 4096 | embedding dimension; must match the model's output |

### `agent`

| Field | Default | Purpose |
|---|---|---|
| `default_model` | `"gpt-4o"` | default model ID (litellm format) |
| `default_provider` | null | fallback provider when prefix matching fails (with `unknown_prefix_policy: "default"`) |
| `unknown_prefix_policy` | `"fail"` | unknown model prefix behavior: `"fail"` (raise) / `"default"` |
| `max_iterations` | 50 | max agent loop iterations per run |
| `max_context_tokens` | 128000 | model context window (used to compute compaction trigger) |
| `compaction_threshold` | 0.8 | trigger compaction at 80% context usage |
| `providers` | `{}` | see `providers` subsection |
| `timeouts.run_seconds` | 300 | total per-run timeout |
| `timeouts.idle_seconds` | 60 | idle timeout (no token output) |
| `timeouts.tool_seconds` | 120 | per-tool-call timeout |
| `timeouts.compaction_seconds` | 900 | compaction task timeout |
| `retry.planning_only_limit` | 1 | "plan-without-action" retries |
| `retry.reasoning_only_limit` | 2 | "reasoning-without-tool-call" retries |
| `retry.empty_response_limit` | 1 | empty response retries |
| `retry.unknown_tool_threshold` | 3 | abort after N unknown-tool calls |
| `compaction.model` | null | compaction model; null uses default_model |
| `compaction.historyThreshold` | 0.8 | history-fraction trigger (synonymous with `agent.compaction_threshold`) |
| `compaction.keep_recent_tokens` | 20000 | tokens preserved verbatim after compaction |
| `compaction.truncate_after_compaction` | false | drop old messages after compaction |
| `tools.max_output_chars` | 25000 | tool output truncation threshold |
| `promptBudget.system_zone_tokens` | 4096 | system prompt token budget |
| `promptBudget.dynamic_zone_tokens` | 4096 | dynamic-zone (memory) token budget |
| `promptBudget.output_reserve_ratio` | 0.3 | output token reservation ratio |

### `agent.providers.<name>`

| Field | Purpose |
|---|---|
| `apiKey` | API key |
| `baseURL` | custom endpoint |
| `prefixes` | list[str], model-ID prefix routing; e.g. `["anthropic"]` routes `anthropic/...` here |
| `models.<id>.modalities.input` | list[str] / set, e.g. `["text", "image", "pdf"]` |
| `models.<id>.modalities.output` | list[str], typically `["text"]` |
| `litellmProvider` / `litellm_provider` | force the litellm provider string |

### `channels.web`

| Field | Default | Purpose |
|---|---|---|
| `enabled` | false | master switch |
| `jwtSecret` / `jwt_secret` | `"change-me-in-production"` | JWT signing secret; **must change in production** |
| `adminToken` / `admin_token` | `""` | admin token for `/api/admin/*` |
| `heartbeatInterval` / `heartbeat_interval` | 30 | WS heartbeat (seconds) |
| `pongTimeout` / `pong_timeout` | 10 | pong timeout (seconds); past this, the connection is considered dead |
| `maxConnectionsPerUser` / `max_connections_per_user` | 3 | concurrent WS cap per account |
| `defaultPermissionTier` / `default_permission_tier` | `"approval"` | Tier governing tool autonomy: `read-only` / `approval` / `yolo` |
| `toolApprovalTimeoutSeconds` / `tool_approval_timeout_seconds` | `60` | Seconds before auto-denying a pending approval |
| `toolsRequiringApproval` / `tools_requiring_approval` | `["bash", "write", "edit"]` | tools that trigger an approval modal in `approval` tier |
| `allowedTools` / `allowed_tools` | `["read"]` | whitelist of tools usable on the web channel |
| `corsOrigins` / `cors_origins` | `["http://localhost:5173"]` | strict CORS allowlist |
| `users` | `[]` | `[{id, password}]`; plaintext (use a hash in production) |

### `channels.feishu`

| Field | Default | Purpose |
|---|---|---|
| `enabled` | false | master switch |
| `appId` / `app_id` | `""` | Feishu app ID |
| `appSecret` / `app_secret` | `""` | Feishu app secret |
| `sessionScope` / `session_scope` | `"chat"` | `"chat"` (per chat) / `"user"` (per user) |
| `groupContext` / `group_context` | `"recent"` | group context mode |
| `groupContextSize` / `group_context_size` | 20 | size for `recent` mode |
| `idleMinutes` / `idle_minutes` | 0 | minutes idle before fresh session; 0 disables |
| `streaming.printFrequencyMs` | 50 | CardKit type interval |
| `streaming.printStep` | 2 | chars per type tick |
| `streaming.printStrategy` | `"fast"` | `"fast"` or `"normal"` |
| `streaming.throttleMs` | 100 | API call throttle |

### `affinity` (Session Affinity Gateway)

| Field | Default | Purpose |
|---|---|---|
| `enabled` | false | master switch; not needed for single instance |
| `ttl_seconds` | 300 | session_key → worker_id mapping TTL |
| `heartbeat_interval` | 30 | worker heartbeat interval |
| `stale_threshold` | 90 | seconds without heartbeat = dead worker |
| `forward_prefix` | `"pyclaw:forward:"` | PubSub forward channel prefix |
| `renewal_interval` | 60 | mapping renewal interval |

### `evolution` + `evolution.curator`

| Field | Default | Purpose |
|---|---|---|
| `enabled` | true | self-evolution master switch |
| `extraction_model` | null | SOP extraction model |
| `min_tool_calls_for_extraction` / `minToolCallsForExtraction` | 2 | min tool calls before extraction is attempted |
| `dedup_overlap_threshold` / `dedupOverlapThreshold` | 0.6 | SOP de-dup overlap threshold |
| `max_sops_per_extraction` / `maxSopsPerExtraction` | 5 | max SOPs produced per extraction |
| `description_max_chars` / `descriptionMaxChars` | 150 | SOP description length cap |
| `procedure_max_chars` / `procedureMaxChars` | 5000 | SOP procedure length cap |
| `curator.enabled` | true | Curator background task switch |
| `curator.checkIntervalSeconds` | 3600 | Curator check interval (seconds) |
| `curator.intervalSeconds` | 604800 | Curator full-scan interval (one week) |
| `curator.staleAfterDays` | 30 | days unused before flagging as stale |
| `curator.archiveAfterDays` | 90 | days unused before archiving |
| `curator.graduationEnabled` | true | SOP → SKILL.md graduation switch |
| `curator.promotionMinUseCount` | 5 | min uses before graduation eligible |
| `curator.promotionMinDays` | 7 | min age before graduation eligible |

### `workspaces`

| Field | Default | Purpose |
|---|---|---|
| `default` | `~/.pyclaw/workspaces` | workspace root directory |
| `backend` | `"file"` | only choice today |
| `bootstrapFiles` / `bootstrap_files` | `["AGENTS.md"]` | files copied into a fresh workspace |

### `skills`

| Field | Default | Purpose |
|---|---|---|
| `workspaceSkillsDir` / `workspace_skills_dir` | `"skills"` | workspace-scoped skills |
| `projectAgentsSkillsDir` / `project_agents_skills_dir` | `".agents/skills"` | project-scoped skills |
| `managedSkillsDir` / `managed_skills_dir` | `~/.openclaw/skills` | ClawHub-installed skills |
| `personalAgentsSkillsDir` / `personal_agents_skills_dir` | `~/.agents/skills` | user-scoped skills |
| `bundledSkillsDir` | null | skills shipped with the PyClaw binary |
| `clawhubBaseUrl` | `https://clawhub.ai` | ClawHub API URL |
| `maxSkillsInPrompt` | 150 | system-prompt skill list cap |
| `maxSkillsPromptChars` | 18000 | system-prompt skill section char cap |
| `maxSkillFileBytes` | 256000 | per-SKILL.md byte cap |
| `progressiveDisclosure` | true | progressive-disclosure switch |

### Top level

| Field | Default | Purpose |
|---|---|---|
| `admin_user_ids` (or `admin.userIds`) | `[]` | user IDs the backend treats as admin |
| `shutdownGraceSeconds` / `shutdown_grace_seconds` | 30 | graceful shutdown timeout (matches K8s default SIGTERM→SIGKILL window) |

---

## Environment-variable overrides

Each Settings class has an env prefix; setting the env var overrides the field:

| Field | Env var |
|---|---|
| `redis.host` | `PYCLAW_REDIS_HOST` |
| `redis.port` | `PYCLAW_REDIS_PORT` |
| `storage.session_backend` | `PYCLAW_STORAGE_SESSION_BACKEND` |
| `memory.base_dir` | `PYCLAW_MEMORY_BASE_DIR` |
| `agent.default_model` | `PYCLAW_AGENT_DEFAULT_MODEL` |
| `server.host` | `PYCLAW_SERVER_HOST` |
| `server.port` | `PYCLAW_SERVER_PORT` (or just `PORT`) |
| `affinity.enabled` | `PYCLAW_AFFINITY_ENABLED` |

Env-var values **override** JSON values. Recommended split for production: put
secrets (jwtSecret / API keys / DB passwords) in env vars, put behavioral
config (timeouts / quotas / model lists) in JSON.

**Note**: nested structures like `agent.providers.<name>.apiKey` are awkward
to override via env; prefer JSON or template rendering with `envsubst` for
those.

---

## See also

- [Deployment guide](./deployment.md) — `docker compose up` for a 3-instance cluster
- [Architecture decisions](./architecture-decisions.md) — design rationale (D1-D26)
- [`configs/pyclaw.example.json`](../../configs/pyclaw.example.json) — complete runnable example
