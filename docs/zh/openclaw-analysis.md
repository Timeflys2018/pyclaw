# OpenClaw 架构分析

针对 [OpenClaw](https://github.com/openclaw/openclaw) 的分析，作为 PyClaw 重写的参考。

> **来源**：本文描述**上游 `openclaw/openclaw`**（HEAD: `388019f5b6`）。
> 明确标注「**本地 fork 观察**」的小节指代一个基于上游的内部本地 fork（非公开），它额外添加了 Redis 原生 session 存储，不代表上游行为。

## 项目概况

- TypeScript monorepo，17,200+ 文件，133 个 extension
- 多 channel AI 助手网关（25+ 消息平台）
- 基于 `@mariozechner/pi-coding-agent` + `@mariozechner/pi-agent-core`（专有库）
- MIT 许可，357K+ stars

## 核心架构（上游）

```
Gateway (HTTP/WS, 端口 18789)
├── Channels（25+ 消息平台，通过 plugin registry）
├── Agent Runtime (pi-embedded-runner + run/)
│   ├── 外层循环 (run.ts): 重试、failover、压缩协调
│   └── 内层循环 (run/attempt.ts): session.prompt() → LLM → tools → 循环
├── Session 存储（**文件系统** — JSONL 文件 + fs.FileHandle 文件锁）
├── Context Engine (src/context-engine/ — 可插拔的 assemble/ingest/compact/maintain)
├── Memory 系统 (SQLite + embeddings，作为 extension/plugin)
├── Dreaming (3 阶段记忆整理: light/deep/REM)
├── Skills (ClawHub 生态，SKILL.md 格式)
└── 配置 (~/.openclaw/openclaw.json)
```

## Session 管理（上游）

### 数据模型（DAG 树）
- SessionHeader: version=3, id (UUID), cwd (文件系统路径), timestamp
- SessionEntry 子类型: message, compaction, branch_summary, thinking_level_change, model_change, custom, label
- 每个 entry 有 id（8 字符 hex）和 parent_id
- Leaf 指针跟踪当前对话头
- Entry 永不修改或删除 — 树只增长

### 存储（上游 = 文件系统）
- Session 以 **JSONL 文件**形式存在磁盘
- 路径由 `cwd` + agent workspace 约定推导
- 写锁：`src/agents/session-write-lock.ts` 使用 **fs.FileHandle** + **`/proc/pid/stat` 的 PID 检测**来识别 stale lock — 纯本地

### cwd 耦合（PyClaw 必须重新设计）
- SessionHeader 里的 `cwd` = agent 工作空间目录（不是 process.cwd）
- pi-coding-agent 的 SessionManager 与文件系统 JSONL 路径紧耦合
- PyClaw 替换为 `workspace_id`（逻辑标识符） + 自有的内存 DAG + 可插拔存储后端

---

### 本地 fork 观察：Redis 原生 session 存储

一个基于上游的内部本地 fork 添加了一套**上游不存在**的 Redis 持久化层：

```
session:{id}:header   → String (JSON)
session:{id}:entries  → Hash<entryId, JSON>
session:{id}:order    → List (追加顺序)
session:{id}:leaf     → String (leaf entryId)
session-lock:{id}     → String (分布式锁)
```

关键文件（本地 fork 独有，上游没有）：
- `redis-session-adapter.ts` — 将 pi-coding-agent 的 SessionManager 包装成 Redis 读写
- `redis-session-keys.ts` — key schema 辅助
- `open-redis-session.ts` — session 打开入口
- `session-write-lock-redis.ts` — Redis `SET NX PX` + Lua CAS 释放、TTL/3 续期、通过 instance_id 前缀检测重入

该 adapter 仍然需要创建 tmpfile 来喂给 pi-coding-agent 的文件系统 SessionManager。

**对 PyClaw 的意义**：PyClaw 借鉴"Redis 后端 session 实现存算分离"的*思路*，但自己设计 key schema、避开 tmpfile hack、拥有完整技术栈（不依赖 pi-coding-agent）。

## Agent Loop (pi-embedded-runner)

### 两层嵌套循环
1. **外层** (`run.ts`): while(true) 带重试/failover/压缩。最大迭代：32-160。
2. **内层** (在 `session.prompt()` 内): LLM 调用 → 工具执行 → 循环直到无 tool_calls。

### Stream 函数链（14 层）
大部分处理多供应商格式差异（litellm 消除了这些需要）：
1. Provider stream override
2. WebSocket transport
3. Text transforms
4. LLM call diagnostics ← **保留**
5. Drop thinking blocks
6. Sanitize tool call IDs
7. Yield abort guard
8. Sanitize malformed tool calls ← **保留**
9. Trim unknown tool names ← **保留**
10. Repair tool call arguments
11. Decode HTML entities (xAI)
12. Anthropic payload logging
13. Sensitive stop reason recovery
14. Idle timeout ← **保留**

PyClaw 保留 3 层（diagnostics、sanitize、idle timeout）。litellm 处理其余。

### 系统 Prompt 组装（30+ 个 section）
关键 section 按顺序：
1. 身份行
2. Tooling（可用工具）
3. Skills（`<available_skills>` XML）
4. 安全规则
5. Memory（通过插件 hook）
6. Workspace 上下文
7. Bootstrap 文件（AGENTS.md 等 — 单文件 12K，总计 60K 预算）
8. 运行时信息（model, timestamp, agent）
9. Cache boundary 标记（用于 Anthropic prompt caching）

### 工具（完整清单）
基础: read, write, edit, grep, find, ls, exec (bash), process
OpenClaw 额外: canvas, nodes, cron, message, tts, image_generate, web_search, web_fetch, sessions_spawn, subagents 等

PyClaw Phase 1: bash, read, write, edit（4 个工具）。

## Context Engine

Agent 循环和上下文管理策略之间的可插拔接口。

### 接口
```
bootstrap() → 初始化 engine 状态
assemble()  → 在 token 预算内组装模型上下文
ingest()    → 把消息捕获到 engine 存储
compact()   → 减少上下文 token 使用
afterTurn() → 回合后生命周期工作
maintain()  → Transcript 维护
```

### 默认 (LegacyContextEngine)
- assemble: pass-through
- ingest: no-op
- compact: 委托给 runtime 压缩
- afterTurn: no-op

第三方 engine 可以实现 RAG 注入、自定义压缩等。

## Memory 系统

放在 `extensions/memory-core/`（不在核心 — 是插件）。

### 存储
- SQLite + sqlite-vec（向量）+ FTS5（全文）
- 路径: `{workspace}/.memory/index.sqlite`
- 来源: MEMORY.md, memory/*.md, session transcripts

### Hooks
- `before_prompt_build` → 自动召回相关记忆
- `llm_output` → 自动捕获重要信息

### 混合搜索
- 向量相似度（cosine，权重 0.7）+ FTS（BM25，权重 0.3）
- 时间衰减、MMR 多样性、可配置阈值

## Dreaming 系统

后台记忆整理（cron 调度）：
- **Light**: 每 6 小时。去重 + 候选 staging。
- **Deep**: 每天凌晨 3 点。LLM 驱动的长期记忆提升。
- **REM**: 每周。跨记忆模式发现。

状态存在 `memory/.dreams/`（文件系统 — PyClaw 必须重新设计）。

## Skill 系统

### 格式
```
skills/{name}/SKILL.md
```
YAML frontmatter（name, description, metadata.openclaw）+ Markdown 正文（注入 agent 系统 prompt）。

### ClawHub Registry (https://clawhub.ai)
- 13,000+ 技能，MIT-0 许可
- REST API: `/api/v1/skills`, `/api/v1/search`, `/api/v1/download`
- 下载格式: ZIP 归档
- 认证: Bearer token（环境变量或 ~/.config/clawhub/config.json）
- 安装到: `{workspace}/skills/{slug}/SKILL.md`
- Lockfile: `.clawhub/lock.json`

### 发现顺序（高 → 低优先级）
1. Workspace 技能 (`{workspace}/skills/`)
2. 项目 agent 技能 (`{workspace}/.agents/skills/`)
3. 个人 agent 技能 (`~/.agents/skills/`)
4. 托管技能 (`~/.openclaw/skills/`)
5. Bundled 技能（随二进制发布）
6. 额外目录 + 插件技能

### Prompt 预算
- 最多 150 个技能进 prompt
- 最多 18,000 字符
- 单 SKILL.md 最大 256KB
- 超预算时降级为紧凑格式（无描述）

## Channel 系统

### ChannelPlugin 接口（~30 个 adapter slot）
必须: id, meta, config
可选: gateway, outbound, security, messaging, threading, directory, streaming, lifecycle 等

### 消息流
```
平台 webhook → Channel Monitor → InboundMessage 归一化
  → dispatchInboundMessage() → Agent 处理
  → ReplyPayload → Channel outbound adapter → 平台 API
```

### 飞书实现
- 插件清单: `openclaw.plugin.json`
- Webhook/长轮询接收入站
- 飞书开放 API 出站
- 支持 DM + 群组 @mention
