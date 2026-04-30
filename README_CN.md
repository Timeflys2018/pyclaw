# PyClaw

基于 [OpenClaw](https://github.com/openclaw/openclaw) 重新设计的 Python 实现，从零构建，核心目标：**存算分离**、**水平扩展**、**模块化架构**。

## 为什么做 PyClaw？

OpenClaw 是一个优秀的多通道 AI 助手 — 但它的 TypeScript 单体架构（17,000+ 文件）将计算和存储紧密耦合，难以在单机之外扩展。PyClaw 吸取 OpenClaw 的精华，用生产级架构重新构建：

**存算分离** — 核心计算层完全无状态。Session、Memory、Dreaming 状态全部存储在共享存储中（Redis、PostgreSQL）。启动 N 个实例放在负载均衡后面即可工作。

**水平扩展** — Session 亲和路由、Redis 分布式写锁、后台任务 Leader 选举。没有单点计算故障。

**模块化设计** — 每一层都是 Python Protocol 接口。开发时用文件存储，生产时切 Redis/PostgreSQL — 改配置，不改代码。新增 Channel 不需要动核心。

**企业与个人兼顾** — 同一套代码，既能单进程零依赖运行（个人用），也能多实例 Redis Cluster + PostgreSQL HA 集群部署（企业用）。

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│  计算层 (无状态)                                                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                       │
│  │ Worker 1 │  │ Worker 2 │  │ Worker N │  ← 水平扩展            │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                       │
└───────┼──────────────┼─────────────┼─────────────────────────────┘
        │              │             │
        ▼              ▼             ▼
┌─────────────────────────────────────────────────────────────────┐
│  存储层 (共享)                                                    │
│  ┌─────────┐  ┌──────────────┐  ┌──────────┐  ┌─────────────┐  │
│  │  Redis  │  │  PostgreSQL  │  │  Memory  │  │   Config    │  │
│  │Sessions │  │  + pgvector  │  │  Store   │  │   Store     │  │
│  │ + Locks │  │  (向量检索)   │  │          │  │             │  │
│  └─────────┘  └──────────────┘  └──────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 核心特性

| 特性 | 说明 |
|------|------|
| **存算分离** | 无状态计算 + 共享存储 = 真正的水平扩展 |
| **可插拔后端** | Session: Redis/文件，Memory: PG/SQLite，Lock: Redis/flock |
| **多通道** | 飞书 + Web（HTTP/WebSocket），通过 Protocol 自由扩展 |
| **ClawHub 兼容** | 直接使用 [ClawHub](https://clawhub.ai) 技能生态（13,000+ skills） |
| **Dreaming 引擎** | 三阶段记忆整理（浅睡/深睡/REM），分布式调度 |
| **多模型支持** | OpenAI、Anthropic、Google、Ollama 等 100+ 模型通过 LiteLLM 接入 |
| **Session 亲和** | 粘性路由 + 实例故障自动转移 |
| **分布式锁** | Redis SET NX PX + Lua CAS — 经过验证的并发控制 |

## 项目结构

```
src/pyclaw/
├── core/                 # 计算层（无状态，最小核心）
│   ├── agent/            # LLM 循环、工具执行、系统提示词、上下文压缩
│   └── hooks.py          # 插件 Hook 接口（可扩展性入口）
├── plugins/              # 可选能力（核心零依赖）
│   ├── memory/           # Memory 插件 — Embedding、分块、混合搜索
│   └── dreaming/         # Dreaming 插件 — 浅睡/深睡/REM 记忆整理
├── storage/              # 存储层（可插拔后端）
│   ├── protocols.py      # Protocol 接口定义（自由切换后端）
│   ├── session/          # Redis + 文件 后端
│   ├── memory/           # PostgreSQL + SQLite 后端
│   └── lock/             # Redis + 文件 后端
├── channels/             # 通道层（可扩展）
│   ├── feishu/           # 飞书 webhook + API
│   └── web/              # HTTP API + WebSocket
├── skills/               # ClawHub 兼容层
│   ├── parser.py         # SKILL.md 解析
│   ├── discovery.py      # 本地技能扫描
│   └── clawhub_client.py # ClawHub REST API 客户端
├── infra/                # 基础设施（Redis、配置、日志）
└── orchestration/        # 健康检查、实例生命周期
```

## 快速开始

```bash
# 克隆
git clone https://github.com/Timeflys2018/pyclaw.git
cd pyclaw

# 安装（需要 Python 3.12+）
pip install -e ".[dev]"

# 启动（开发模式 — 无需 Redis/PG）
pyclaw
```

## 部署模式

### 个人使用（单机）
```yaml
storage:
  session_backend: file
  memory_backend: sqlite
  lock_backend: file
```
零外部依赖。只要有 Python 就能跑。

### 企业部署（多实例）
```yaml
storage:
  session_backend: redis
  memory_backend: postgres
  lock_backend: redis
```
N 个 Worker 放在负载均衡后面。Redis 处理热数据。PostgreSQL 持久化记忆。

## 技能生态兼容

PyClaw 完全兼容 OpenClaw 的 [ClawHub](https://clawhub.ai) 技能生态：

- 读取相同的 `SKILL.md` 格式（YAML frontmatter + Markdown 指令）
- 与 OpenClaw 共享技能目录（`~/.openclaw/skills/`）
- 相同的 ClawHub REST API 客户端（搜索、安装、更新）
- 任一工具安装的技能对双方都可用

## 与 OpenClaw 的关系

PyClaw 受 [OpenClaw](https://github.com/openclaw/openclaw) 启发，并设计为与其 [ClawHub](https://clawhub.ai) 技能生态兼容。PyClaw 是**独立的 Python 重新实现**，不是 fork。它继承了领域模型（Session、Memory、Dreaming、Channel、Skill），但为存算分离和水平扩展重新设计了架构。

## 参与贡献

欢迎 PR。项目使用 OpenSpec 管理架构规范和任务分解。

## 许可证

MIT
