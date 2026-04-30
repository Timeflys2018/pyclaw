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
