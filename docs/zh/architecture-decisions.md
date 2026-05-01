# 架构决策记录

## D1: Python + FastAPI + asyncio

Python 3.12+ 配合 FastAPI (ASGI) + uvicorn 作为运行时栈。

**理由**：AI/LLM 生态以 Python 为主（litellm、langchain、sentence-transformers）。FastAPI 原生异步、自动生成 OpenAPI 文档、支持 WebSocket。asyncio 对 I/O 密集型负载（LLM 调用、Redis 操作）完全够用。

## D2: Redis 作为 Session 主存储

生产环境用 Redis（Hash + List + Sorted Set），开发环境用文件后端。

> **来源说明**：上游 `openclaw/openclaw` 使用**基于文件系统**的 session 存储（JSONL 文件 + fs.FileHandle 文件锁）。Redis 分布式 session 层是 **PyClaw 自主设计**，借鉴了内部 fork 采用 Redis 的思路，但 key schema 与写入协议由 PyClaw 自行决定。存算分离的目标要求 session 移出文件系统。

**PyClaw Redis key schema**（PyClaw 自己的设计）：
```
session:{id}:header   → String (JSON)
session:{id}:entries  → Hash<entryId, JSON>
session:{id}:order    → List<entryId>
session:{id}:leaf     → String (当前 leaf entryId)
session-lock:{id}     → String (锁值, SET NX PX)
session-affinity:{id} → String (instance_id, TTL 5 分钟)
```

## D3: PostgreSQL + pgvector 作为生产 Memory 存储

生产用 PG+pgvector，开发用 SQLite+sqlite-vec。一个依赖提供 ACID、FTS（tsvector）和向量检索。

## D4: 乐观并发 + 写时互斥

读无锁。写时获取 Redis 分布式锁（SET NX PX + Lua CAS 释放/续期）。锁在 TTL/3 间隔续期。

## D5: ChannelPlugin 作为 Python Protocol + adapter slots

Channel 只实现需要的 adapter（gateway, outbound, messaging 等）。新增 channel 无需改动核心。

## D6: ClawHub 兼容 - 共享目录 + REST API

从 `~/.openclaw/skills/` 读取技能（与 TypeScript OpenClaw 共享）。Python httpx 原生客户端调用 ClawHub API。

## D7: LiteLLM 统一多供应商 LLM 访问

统一 100+ 供应商接口。处理 OpenClaw 用 14 层 stream middleware 解决的供应商格式差异。

## D8: 独立的 models/ 层

`src/pyclaw/models/` 作为共享数据模型层。`core/` 和 `storage/` 都依赖 models/，但互不依赖。

## D9: AsyncGenerator 流式 API

`run_agent_stream()` 返回 `AsyncGenerator[AgentEvent, None]`。AgentEvent 联合类型：TextChunk | ToolCallStart | ToolCallEnd | Done | Error。Phase 2 有需要时通过 asyncio.Queue 增加多消费者广播。

## D10: Write-through Session 持久化

每次 `append_entry()` 立即持久化到存储后端。Crash-safe：worker 中途崩溃时，已写入的 entry 对其他 worker 可见。

## D11: 智能混合工具执行

工具声明 `side_effect: bool`。`side_effect=False`（read）通过 `asyncio.gather` 并行执行。`side_effect=True`（bash, write, edit）顺序执行。

## D12: ContextEngine Protocol + 默认 pass-through

定义在 `core/context_engine.py`。Agent runner 始终通过它调用。Phase 1：DefaultContextEngine（pass-through）。Phase 2：换成 mem0/langchain 实现，runner 零改动。

## D13: Workspace 配置映射

`pyclaw.json` 里 `workspaces` 字段把 workspace_id 映射到文件系统路径。不同机器可以把同一个 workspace_id 映射到不同路径。

```json
{
  "workspaces": {
    "default": ".",
    "my-project": "/path/to/project"
  }
}
```

## D14: JSON 配置格式（非 YAML）

JSON 作为主配置格式以兼容 OpenClaw。加载顺序：`pyclaw.json` → `configs/pyclaw.json` → `~/.openclaw/pyclaw.json`。环境变量可覆盖。

## D15: Memory 和 Dreaming 作为插件（非核心）

`plugins/memory/` 和 `plugins/dreaming/` — 核心层零依赖。Agent 在个人轻量模式下可以不启用 memory/dreaming 运行。通过 hooks + ContextEngine 注入。

## D16: 单循环 Agent 设计

一个显式循环：`assemble_prompt → call_llm → process_response → (有 tool_calls? execute_tools → 循环 : done)`。OpenClaw 的嵌套设计是因为 `session.prompt()` 是不透明的；我们拥有完整技术栈。

## D17: Session DAG 树（非扁平列表）

Session 是 append-only DAG 树。每个 entry 有 id 和 parent_id。Leaf 指针跟踪当前对话头。Compaction 创建新分支携带摘要。`build_session_context()` 从 leaf 走到 root 生成 LLM 需要的扁平消息列表。

## D18: 单一权威 `SessionStore` Protocol

`SessionStore` Protocol 在整个仓库中仅有一处定义：`src/pyclaw/storage/session/base.py`。它基于类型化的 `SessionTree` 与 `SessionEntry` 操作（而非原始 dict）。

**背景**：早期实现中存在两个冲突的 Protocol —— `storage/protocols.py` 中基于 dict 的变体与 `storage/session/base.py` 中的类型化变体。后端实现者必须二选一；Runner 始终使用类型化版本。dict 变体属于死代码，形成迁移风险。

**整合成果**（harden-agent-core Group 2）：
- `storage/protocols.py` 现在从 `session/base.py` re-export 类型化 `SessionStore`，不再并行定义。
- `storage/__init__.py` 从同一路径导出 `SessionStore`。
- 以下三种 import 路径均解析到同一个类：
  - `from pyclaw.storage import SessionStore`
  - `from pyclaw.storage.protocols import SessionStore`
  - `from pyclaw.storage.session.base import SessionStore`

**Protocol 接口**：
```python
class SessionStore(Protocol):
    async def load(self, session_id: str) -> SessionTree | None: ...
    async def save_header(self, tree: SessionTree) -> None: ...
    async def append_entry(self, session_id: str, entry: SessionEntry, leaf_id: str) -> None: ...
```

## D19: SessionKey / SessionId 两层分离

会话系统使用两个概念，而非一个 ID 身兼多职：

| 概念 | 职责 | 格式 | 生命周期 |
|---|---|---|---|
| **sessionKey** | 路由地址，由渠道上下文确定 | `feishu:{app_id}:{scope_id}` | 永久稳定 |
| **sessionId** | 存储容器，持有实际对话内容 | `{sessionKey}:s:{8hex}` | 随 `/new` 轮换 |

**背景**：最初 `session_id` 既作路由地址又作存储 key（如 `feishu:cli_xxx:ou_abc`）。这使 `/new` 无法实现——改变存储 key 就等于改变了路由地址。

**设计决策**：
- sessionKey 保持稳定，存入 `pyclaw:skey:{sessionKey}:current`（STRING）指向当前活跃 sessionId
- 历史 sessionId 通过 `pyclaw:skey:{sessionKey}:history`（ZSET，score=创建时间ms）归档
- `/new` 只换 sessionId，旧 sessionId 的全部 Redis keys 完整保留，永不删除
- `SessionRouter` 封装路由逻辑：sessionKey → sessionId → SessionTree
- 懒迁移兼容：旧格式 session（sessionId == sessionKey）在首次访问时自动注册到新索引，零停机

**被排除的替代方案**：
- 只用 sessionKey（无 sessionId）：历史会话只能靠时间戳命名，无法原子归档
- 批量迁移脚本：需要停机协调，旧 session 多时风险高

**skey 索引键无 TTL**：`skey:current` 和 `skey:history` 永久保留（不设 EXPIRE）；每条对话的 header/entries/order/leaf 依然用滑动 TTL（默认 30 天）。索引键指向已过期的 session 数据是合理的（`/history` 展示"已归档"状态）。

**相关实现**：`implement-session-key-rotation` change，`src/pyclaw/channels/session_router.py`

## D20: 命令拦截在渠道层，Agent 层无感知

飞书命令（`/new`、`/status`、`/whoami` 等）在 `handle_feishu_message()` 中被拦截，直接由命令处理器回复，**不进入 agent runner**。

**理由**：
- 命令应在毫秒内完成，不需要 LLM 推理
- 保持 agent runner 职责单一（只处理普通对话）
- 命令回复用纯文字，无需 `cardkit:card:write` 权限
- 新增命令只需改渠道层，核心零改动

**命令集**（essential tier）：`/new`、`/reset`、`/status`、`/whoami`、`/history`、`/help`、`/idle <Xm>`

**未识别的 `/` 前缀消息**直接透传给 agent，LLM 自行处理。

**相关实现**：`src/pyclaw/channels/feishu/commands.py`

## D21: 空闲自动重置跟踪 last_interaction_at

`SessionHeader` 持有 `last_interaction_at: str | None`（UTC ISO 时间戳），每次用户消息被 agent 处理后更新。系统事件（命令回复、心跳）不更新此字段。

**idle_minutes** 配置来源（优先级由高到低）：
1. 用户通过 `/idle 30m` 设置的 per-session 覆盖值（存于 `SessionHeader.idle_minutes_override`）
2. `FeishuSettings.idle_minutes`（全局默认，默认 0 = 关闭）

**默认关闭**：与 OpenClaw 保持一致（`DEFAULT_IDLE_MINUTES = 0`）。启用后，用户超时未发消息时下一条消息到来会静默触发 `/new`，新旧 session 均完整保留。
