# 配置参考

PyClaw 用一个 JSON 配置文件 (`pyclaw.json`) 描述运行时所有可调项 — Redis、模型 provider、记忆系统、agent 行为、channel、技能、自演化、affinity 等。本文档**按使用场景**组织, 5 个常见场景给最小可用配置, 后面附完整字段表。

## 目录

- [配置文件位置](#配置文件位置)
- [场景 1: 本地开发, 零依赖](#场景-1-本地开发零依赖)
- [场景 2: 生产单实例 (Web channel)](#场景-2-生产单实例-web-channel)
- [场景 3: 生产多实例 (active-active)](#场景-3-生产多实例-active-active)
- [场景 4: 接 Feishu (Lark) 机器人](#场景-4-接-feishu-lark-机器人)
- [场景 5: 启用记忆 + 自演化](#场景-5-启用记忆--自演化)
- [完整字段表](#完整字段表)
- [环境变量覆盖](#环境变量覆盖)

---

## 配置文件位置

PyClaw 启动时按以下顺序查找配置文件:

1. `pyclaw.json` (当前工作目录)
2. `configs/pyclaw.json` (项目目录)
3. `~/.openclaw/pyclaw.json` (用户目录)

第一个存在的文件被加载。都不存在时使用默认值 (Web/Feishu 都禁用, 单进程内存模式)。

完整模板参考 [`configs/pyclaw.example.json`](../../configs/pyclaw.example.json) (167 行, 涵盖所有可选字段)。

---

## 场景 1: 本地开发, 零依赖

最小开发配置 — 不需要 Redis, 不需要外部数据库, 直接 `./scripts/start.sh` 跑起来:

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

**关键点**:
- `storage.session_backend: "memory"` — 进程内 dict 存会话; 重启丢
- `storage.lock_backend: "file"` — 文件锁, 走 `~/.pyclaw/locks/` (单进程够用)
- `agent.providers.anthropic.models.<id>.modalities` — **声明模型支持的输入/输出**。`["text", "image"]` 表示这个模型可以接图片; agent runner 在用户上传图片时会校验, 不支持就返回 `vision_not_support` 错误
- `channels.web.users` — 明文用户名/密码, 仅供本地; 生产**不要**这样写

---

## 场景 2: 生产单实例 (Web channel)

跑一个 PyClaw 实例 + 一个 Redis, 处理几十到上百用户的 web 请求:

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

**关键点**:
- `storage.session_backend: "redis"` — 会话写 Redis; 重启不丢, 多实例可共享 (见场景 3)
- `storage.lock_backend: "redis"` — 分布式锁 (基于 SET NX PX + Lua CAS), 防同一会话被两个 worker 并发处理
- `redis.keyPrefix` — 多个 PyClaw 部署共享同一 Redis 时, 用前缀做命名空间隔离
- `web.jwtSecret` / `adminToken` — 用 `${ENV}` 占位符是约定俗成做法, 但 PyClaw **不自动**做 env 展开。两种实际做法:
  1. 启动前用 `envsubst` 模板渲染
  2. 用环境变量直接覆盖 (见 [环境变量覆盖](#环境变量覆盖))
- `web.maxConnectionsPerUser: 3` — 同账号同时 3 个 WebSocket; 防滥用
- `web.defaultPermissionTier` — `"read-only" | "approval" | "yolo"`(默认
  `"approval"`)。Read-only 直接拒写类工具,approval 门控下方列表,yolo 跳过门控。
  详见[权限指南](./permissions.md)
- `web.toolApprovalTimeoutSeconds` — 用户多少秒不响应自动拒(默认 `60`)
- `web.toolsRequiringApproval` — 列表里的工具调用前会弹窗等用户确认; 写入类工具默认在列(`["bash", "write", "edit"]`)
- `web.corsOrigins` — 严格 CORS, 必须列出真实前端域名

---

## 场景 3: 生产多实例 (active-active)

3 个 PyClaw worker + 1 个 Redis + 1 个 nginx (ip_hash + Session Affinity Gateway), 横向扩展:

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

**关键点**:
- `affinity.enabled: true` — 启用 Session Affinity Gateway。每个 worker 启动时在 Redis 注册自己 (`pyclaw:workers` zset), 每个会话首次到达时锁定到一个 worker (`session_key → worker_id` mapping); 之后哪怕负载均衡把请求路到别的 worker, 也会通过 Redis PubSub 转发回 owner worker
- `affinity.ttl_seconds: 300` — affinity 记录的 TTL (秒); worker 心跳每 `renewal_interval` 秒续期一次
- `affinity.stale_threshold: 90` — worker 多少秒没心跳就视为死亡; gateway 的 PUBLISH-subscriber-count 检测会触发 force_claim 故障转移
- 所有 3 个 worker 用**同一份** `pyclaw.json` (volume mount read-only); 没有 worker-specific 配置
- nginx 必须配 `ip_hash` (见 [`deploy/nginx.conf`](../../deploy/nginx.conf)) — 这是性能优化 (减少跨 worker 转发); affinity gateway 是正确性保证 (即便 nginx 错路也对)

启动方式见 [部署指南](./deployment.md#多实例-docker)。

---

## 场景 4: 接 Feishu (Lark) 机器人

Feishu channel 用长连接 WebSocket (Feishu 集群模式), 不需要公网入口。Web channel 可以同时开:

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

**关键点**:
- `appId` / `appSecret` — Feishu 开放平台应用凭证; 在 https://open.feishu.cn 创建机器人后获得
- `sessionScope: "chat"` — 每个聊天 (单聊或群聊) 一个 session; 可选 `"user"` (每用户跨群共享)
- `groupContext: "recent"` — 群聊时, 把当前用户的最近 N 条消息作为上下文注入; 可选 `"thread"` (只 thread reply 链) 或 `""` (无)
- `groupContextSize: 20` — `recent` 模式取最近多少条
- `idleMinutes: 0` — 0 表示空闲不重置; 设 30 表示 30 分钟没消息后, 下条消息开新 session
- `streaming` — Feishu CardKit streaming 卡片打字效果参数 (节流 / 步长), 调小 `throttleMs` 看起来更"实时"但 API quota 消耗高

---

## 场景 5: 启用记忆 + 自演化

PyClaw 的招牌特性 — 4 层记忆 (L1 Redis 索引 + L2/L3 SQLite FTS5 + L4 sqlite-vec 归档), 加上自动 SOP 提取与 Curator 生命周期:

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

**关键点**:
- `memory.base_dir` — SQLite 文件存放目录; 多实例部署时**必须挂共享卷** (NFS / EFS), 否则不同 worker 看不到同一份记忆
- `memory.search_l2_quota` / `search_l3_quota` — 每次 prompt 注入的 facts (L2) / procedures (L3) 上限; 调大占 token 多但召回好
- `memory.archive_min_similarity: 0.5` — L4 向量召回的相似度阈值; 低于这个分的归档片段不注入
- `embedding` — L4 归档需要 embedding 服务; 不配置则 `archive_enabled: false`
- `evolution.extraction_model` — SOP 提取专用模型; 用便宜的 Haiku 类模型即可, 不必跟主 agent 同模型
- `evolution.curator.staleAfterDays: 30` / `archiveAfterDays: 90` — Curator 后台任务把 30 天没用过的 SOP 标 stale, 90 天的归档
- `evolution.curator.graduationEnabled: true` — 高频使用的 SOP 自动毕业为 SKILL.md (渐进披露)

---

## 完整字段表

按 Settings 类组织, 列出**实际暴露**的字段、类型、默认值、用途。值的别名 (camelCase JSON ↔ snake_case Python) 同时给出。

### `server`

| 字段 (JSON / Python) | 类型 | 默认 | 说明 |
|---|---|---|---|
| `host` | str | `"0.0.0.0"` | uvicorn 绑定地址 |
| `port` | int | `8000` | uvicorn 端口 |

### `redis`

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `host` | str | `"localhost"` | Redis 主机 |
| `port` | int | `6379` | Redis 端口 |
| `password` | str / null | `null` | 密码; 没设留 `null` |
| `url` | str | `""` | 直接给完整 URL (如 `redis://...`); 设了就忽略 host/port/password |
| `keyPrefix` / `key_prefix` | str | `"pyclaw:"` | 所有 Redis key 的前缀 |
| `transcriptRetentionDays` / `transcript_retention_days` | int | `7` | 会话历史 Redis TTL (天) |

### `storage`

| 字段 | 默认 | 取值 |
|---|---|---|
| `session_backend` | `"memory"` | `"memory"` (单进程 dict) / `"redis"` (生产) |
| `memory_backend` | `"sqlite"` | `"sqlite"` (目前唯一) |
| `lock_backend` | `"file"` | `"file"` (本地) / `"redis"` (分布式) |

### `memory`

| 字段 | 默认 | 说明 |
|---|---|---|
| `base_dir` | `~/.pyclaw/memory` | SQLite 文件目录 |
| `l1_max_entries` | 30 | L1 Redis 工作记忆条数上限 |
| `l1_max_chars` | 3000 | L1 单条最大字符数 |
| `l1_ttl_seconds` | 2592000 | L1 TTL (30 天) |
| `search_l2_quota` | 3 | 每次 prompt 注入 facts 上限 |
| `search_l3_quota` | 2 | 每次 prompt 注入 procedures 上限 |
| `search_fts_min_query_chars` | 3 | FTS 查询最小字符数 (短于此跳过 FTS) |
| `archive_max_results` | 5 | L4 召回上限 |
| `archive_min_similarity` | 0.5 | L4 向量相似度阈值 |
| `archive_min_results` | 1 | L4 最少返回数 (低于阈值仍返回这么多) |
| `archive_enabled` | true | L4 总开关 |
| `namingPolicy` / `naming_policy` | `"human"` | `"human"` (生成可读 ID) / `"hash"` (生成 hash ID) |

### `embedding`

| 字段 | 默认 | 说明 |
|---|---|---|
| `model` | `""` | embedding 模型 ID (litellm 格式, 如 `openai/text-embedding-3-small`) |
| `apiKey` / `api_key` | `""` | API key |
| `baseURL` / `base_url` | `""` | 自定义 endpoint |
| `dimensions` | 4096 | embedding 维度; 必须跟模型本身的输出维度一致 |

### `agent`

| 字段 | 默认 | 说明 |
|---|---|---|
| `default_model` | `"gpt-4o"` | 默认模型 ID (litellm 格式) |
| `default_provider` | null | 找不到 prefix 匹配时的兜底 provider (配合 `unknown_prefix_policy: "default"`) |
| `unknown_prefix_policy` | `"fail"` | 未知模型 prefix 行为: `"fail"` (报错) / `"default"` (走 default_provider) |
| `max_iterations` | 50 | agent loop 单次 run 最大迭代轮数 |
| `max_context_tokens` | 128000 | 模型上下文窗口 (用于 compaction 触发计算) |
| `compaction_threshold` | 0.8 | 上下文用量到 80% 触发 compaction |
| `providers` | `{}` | 见下面 `providers` 子表 |
| `timeouts.run_seconds` | 300 | 单次 run 总超时 |
| `timeouts.idle_seconds` | 60 | 空闲超时 (无 token 输出多久 abort) |
| `timeouts.tool_seconds` | 120 | 单次工具调用超时 |
| `timeouts.compaction_seconds` | 900 | compaction 任务超时 |
| `retry.planning_only_limit` | 1 | "只有 plan 没行动" 重试次数 |
| `retry.reasoning_only_limit` | 2 | "只有 reasoning 没 tool call" 重试次数 |
| `retry.empty_response_limit` | 1 | 空响应重试次数 |
| `retry.unknown_tool_threshold` | 3 | 调未知 tool N 次后 abort |
| `compaction.model` | null | compaction 用的模型 ID; null 用 default_model |
| `compaction.historyThreshold` | 0.8 | 历史长度比例触发 compaction (跟 `agent.compaction_threshold` 同义) |
| `compaction.keep_recent_tokens` | 20000 | compaction 保留最近 token 数 |
| `compaction.truncate_after_compaction` | false | compaction 后是否截断旧消息 |
| `tools.max_output_chars` | 25000 | 工具输出最大字符 (超出截断) |
| `promptBudget.system_zone_tokens` | 4096 | system prompt token 预算 |
| `promptBudget.dynamic_zone_tokens` | 4096 | dynamic 区 (memory 注入) token 预算 |
| `promptBudget.output_reserve_ratio` | 0.3 | 输出预留比例 |

### `agent.providers.<name>`

| 字段 | 说明 |
|---|---|
| `apiKey` | API key |
| `baseURL` | 自定义 endpoint |
| `prefixes` | list[str], 模型 ID 前缀匹配; 例: `["anthropic"]` 让 `anthropic/...` 走这个 provider |
| `models.<id>.modalities.input` | list[str] / set, e.g. `["text", "image", "pdf"]` |
| `models.<id>.modalities.output` | list[str], 通常 `["text"]` |
| `litellmProvider` / `litellm_provider` | 强制指定 litellm 用的 provider 字符串 |

### `channels.web`

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | false | 总开关 |
| `jwtSecret` / `jwt_secret` | `"change-me-in-production"` | JWT 签名 secret; **生产必改** |
| `adminToken` / `admin_token` | `""` | `/api/admin/*` 接口的 admin token |
| `heartbeatInterval` / `heartbeat_interval` | 30 | WS 心跳间隔 (秒) |
| `pongTimeout` / `pong_timeout` | 10 | pong 超时 (秒); 超时认为连接死 |
| `maxConnectionsPerUser` / `max_connections_per_user` | 3 | 同账号最大 WS 连接数 |
| `defaultPermissionTier` / `default_permission_tier` | `"approval"` | 权限 tier:`read-only` / `approval` / `yolo` |
| `toolApprovalTimeoutSeconds` / `tool_approval_timeout_seconds` | `60` | 用户不响应几秒后自动拒 |
| `toolsRequiringApproval` / `tools_requiring_approval` | `["bash", "write", "edit"]` | `approval` tier 下触发审批 modal 的工具 |
| `allowedTools` / `allowed_tools` | `["read"]` | 白名单; 只 web channel 能用的工具 |
| `corsOrigins` / `cors_origins` | `["http://localhost:5173"]` | CORS 允许的源 |
| `users` | `[]` | `[{id, password}]`; 明文密码 (生产请用 hash) |

### `channels.feishu`

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | false | 总开关 |
| `appId` / `app_id` | `""` | Feishu 应用 ID |
| `appSecret` / `app_secret` | `""` | Feishu 应用 secret |
| `sessionScope` / `session_scope` | `"chat"` | `"chat"` (每聊天) / `"user"` (每用户) |
| `groupContext` / `group_context` | `"recent"` | 群聊上下文模式 |
| `groupContextSize` / `group_context_size` | 20 | recent 模式条数 |
| `idleMinutes` / `idle_minutes` | 0 | 空闲多少分钟自动开新 session; 0 = 不重置 |
| `streaming.printFrequencyMs` | 50 | CardKit 打字间隔 |
| `streaming.printStep` | 2 | 每次打字字符数 |
| `streaming.printStrategy` | `"fast"` | `"fast"` 或 `"normal"` |
| `streaming.throttleMs` | 100 | API 调用节流 |

### `affinity` (Session Affinity Gateway)

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | false | 总开关; 单实例不需要 |
| `ttl_seconds` | 300 | session_key → worker_id 映射 TTL |
| `heartbeat_interval` | 30 | worker 心跳间隔 |
| `stale_threshold` | 90 | worker 多久没心跳算死 |
| `forward_prefix` | `"pyclaw:forward:"` | PubSub 转发频道前缀 |
| `renewal_interval` | 60 | 映射续期间隔 |

### `evolution` + `evolution.curator`

| 字段 | 默认 | 说明 |
|---|---|---|
| `enabled` | true | 自演化总开关 |
| `extraction_model` | null | SOP 提取模型 |
| `min_tool_calls_for_extraction` / `minToolCallsForExtraction` | 2 | 至少多少次 tool call 才尝试提取 |
| `dedup_overlap_threshold` / `dedupOverlapThreshold` | 0.6 | SOP 去重重叠度阈值 |
| `max_sops_per_extraction` / `maxSopsPerExtraction` | 5 | 单次提取最多生成多少 SOP |
| `description_max_chars` / `descriptionMaxChars` | 150 | SOP 描述长度上限 |
| `procedure_max_chars` / `procedureMaxChars` | 5000 | SOP 流程长度上限 |
| `curator.enabled` | true | Curator 后台任务总开关 |
| `curator.checkIntervalSeconds` | 3600 | Curator 检查间隔 (秒) |
| `curator.intervalSeconds` | 604800 | Curator 全量扫描间隔 (一周) |
| `curator.staleAfterDays` | 30 | 多少天未使用标 stale |
| `curator.archiveAfterDays` | 90 | 多少天未使用归档 |
| `curator.graduationEnabled` | true | SOP → SKILL.md 毕业开关 |
| `curator.promotionMinUseCount` | 5 | 毕业最少使用次数 |
| `curator.promotionMinDays` | 7 | 毕业最少存在天数 |

### `workspaces`

| 字段 | 默认 | 说明 |
|---|---|---|
| `default` | `~/.pyclaw/workspaces` | workspace 根目录 |
| `backend` | `"file"` | 当前唯一选项 |
| `bootstrapFiles` / `bootstrap_files` | `["AGENTS.md"]` | 创建新 workspace 时复制的文件 |

### `skills`

| 字段 | 默认 | 说明 |
|---|---|---|
| `workspaceSkillsDir` / `workspace_skills_dir` | `"skills"` | workspace 内技能目录 |
| `projectAgentsSkillsDir` / `project_agents_skills_dir` | `".agents/skills"` | 项目级技能 |
| `managedSkillsDir` / `managed_skills_dir` | `~/.openclaw/skills` | ClawHub 安装的技能 |
| `personalAgentsSkillsDir` / `personal_agents_skills_dir` | `~/.agents/skills` | 用户级技能 |
| `bundledSkillsDir` | null | 与 PyClaw 二进制一起打包的技能 |
| `clawhubBaseUrl` | `https://clawhub.ai` | ClawHub API URL |
| `maxSkillsInPrompt` | 150 | system prompt 最多列多少技能 |
| `maxSkillsPromptChars` | 18000 | system prompt 技能段最大字符 |
| `maxSkillFileBytes` | 256000 | 单个 SKILL.md 最大字节 |
| `progressiveDisclosure` | true | 渐进披露开关 (描述+按需加载完整内容) |

### 顶层

| 字段 | 默认 | 说明 |
|---|---|---|
| `admin_user_ids` (或 `admin.userIds`) | `[]` | 后端识别为 admin 的 user ID 列表 |
| `shutdownGraceSeconds` / `shutdown_grace_seconds` | 30 | 优雅关闭超时 (匹配 K8s 默认 SIGTERM→SIGKILL 窗口) |

---

## 环境变量覆盖

每个 Settings 类有 env prefix; 设环境变量可覆盖对应字段:

| 字段 | 环境变量 |
|---|---|
| `redis.host` | `PYCLAW_REDIS_HOST` |
| `redis.port` | `PYCLAW_REDIS_PORT` |
| `storage.session_backend` | `PYCLAW_STORAGE_SESSION_BACKEND` |
| `memory.base_dir` | `PYCLAW_MEMORY_BASE_DIR` |
| `agent.default_model` | `PYCLAW_AGENT_DEFAULT_MODEL` |
| `server.host` | `PYCLAW_SERVER_HOST` |
| `server.port` | `PYCLAW_SERVER_PORT` (或直接 `PORT`) |
| `affinity.enabled` | `PYCLAW_AFFINITY_ENABLED` |

环境变量值会**覆盖** JSON 文件里的值。生产推荐: 把 secrets (jwtSecret / API key / 数据库密码) 放环境变量, 把行为类配置 (timeouts / quota / 模型列表) 放 JSON。

**注意**: 嵌套结构 (如 `agent.providers.<name>.apiKey`) 通过 env 覆盖比较 awkward; 此类字段建议直接写 JSON 或用 `envsubst` 模板渲染。

---

## 相关文档

- [部署指南](./deployment.md) — `docker compose up` 一条命令跑起 3 实例集群
- [架构决策](./architecture-decisions.md) — 配置项背后的设计理由 (D1-D26)
- [`configs/pyclaw.example.json`](../../configs/pyclaw.example.json) — 完整可执行示例
