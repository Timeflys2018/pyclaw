# PyClaw

[中文文档](./README_CN.md)

[![License](https://img.shields.io/badge/license-MIT-green.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/python-3.12%2B-green.svg)](https://www.python.org/)
[![Tests](https://img.shields.io/badge/tests-599%20passed-brightgreen.svg)]()

A Python reimplementation of [OpenClaw](https://github.com/openclaw/openclaw), built from the ground up for **compute-storage separation**, **horizontal scaling**, and **modular architecture**.

## Why PyClaw?

OpenClaw is a powerful multi-channel AI assistant — but its TypeScript monolith (17,000+ files) tightly couples compute and storage, making it hard to scale beyond a single machine. PyClaw takes the best ideas from OpenClaw and rebuilds them with a production-first architecture:

**Compute-Storage Separation** — The core layer is stateless. Sessions live in Redis, workspace config in Redis or files. Spin up N instances behind a load balancer and they just work.

**Horizontal Scaling** — Feishu's native WebSocket cluster mode (up to 50 connections per app), distributed write locks (Redis), session affinity gateway (planned).

**Modular by Design** — Every layer is a Python Protocol. Swap Redis for files in development. Add a new channel without touching core.

**Enterprise & Personal** — Same codebase scales from a single-process dev setup (zero dependencies beyond Python) to multi-instance production with Redis.

## Current Status

| Layer | Status |
|-------|--------|
| **Agent Core** | ✅ LLM loop, tools (bash/read/write/edit), compaction, timeouts, retry |
| **Session Store** | ✅ Redis (production) + InMemory (dev), SessionKey/SessionId rotation |
| **Feishu Channel** | ✅ WebSocket, streaming CardKit reply, commands (/new /status /history) |
| **Workspace** | ✅ FileWorkspaceStore + RedisWorkspaceStore, bootstrap context injection |
| **Context Engine** | ✅ Phase 1 (compaction + bootstrap injection), Phase 2 planned (memory/RAG) |
| **Web Channel** | ✅ Per-user multiplexed WebSocket, REST API, OpenAI-compat SSE, React SPA, cluster observation |
| **Memory/Dreaming** | 🔲 Planned (sqlite-vec / pgvector) |
| **Skill Hub** | ✅ ClawHub compatible: SKILL.md parsing, discovery, eligibility, prompt injection, install CLI |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Compute Layer (Stateless Workers)                               │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Agent Runner  │  Context Engine  │  Tool Registry        │   │
│  │  (LLM loop)   │  (bootstrap+RAG) │  (bash,read,write,ed)│   │
│  └──────────────────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │  Channels                                                 │   │
│  │  ├── Feishu (WS receiver + CardKit streaming + commands) │   │
│  │  └── Web (HTTP + WebSocket) [planned]                     │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  Storage Layer (Shared)                                          │
│  ┌─────────────┐  ┌──────────────────┐  ┌────────────────────┐ │
│  │    Redis     │  │  WorkspaceStore  │  │  Future: PG/SQLite │ │
│  │  Sessions    │  │  (File or Redis) │  │  Memory + Vectors  │ │
│  │  Locks       │  │  Bootstrap files │  │                    │ │
│  │  Affinity    │  │                  │  │                    │ │
│  │  Dedup       │  │                  │  │                    │ │
│  └─────────────┘  └──────────────────┘  └────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Key Features (Implemented)

| Feature | Description |
|---------|-------------|
| **Agent Loop** | Single-loop design: assemble → LLM → tools → repeat. Streaming, abort, retry, compaction |
| **Session Rotation** | `/new` creates fresh session, old archived. SessionKey (stable) / SessionId (rotatable) |
| **Session Commands** | `/new`, `/reset`, `/status`, `/whoami`, `/history`, `/help`, `/idle` |
| **Feishu WebSocket** | Long-connection mode, no public IP needed, auto-reconnect, native cluster (multi-instance) |
| **CardKit Streaming** | Streaming card reply with 160ms throttle, automatic text fallback |
| **Bootstrap Injection** | AGENTS.md (+ SOUL.md, USER.md) injected into system prompt via ContextEngine |
| **Redis Sessions** | DAG tree session model, distributed write locks, sliding TTL |
| **Workspace Store** | File or Redis backend, config-driven selection |
| **Multi-Instance** | Feishu native cluster mode (up to 50 workers), distributed dedup + locks |
| **Skill Hub** | ClawHub-compatible skill ecosystem: SKILL.md parsing, 5-layer discovery, eligibility filtering, prompt injection with budget, `pyclaw-skill` CLI |
| **Web Channel** | Per-user multiplexed WebSocket, JWT auth, streaming chat, tool approval, OpenAI-compat `/v1/chat/completions` SSE, React SPA, cluster observation |

### Web Channel Preview

![PyClaw Web Channel](./docs/assets/web-channel-chat.png?v=2)

## Project Structure

```
src/pyclaw/
├── core/                 # Compute layer (stateless)
│   ├── agent/            # LLM loop, tools, system prompt, compaction, factory
│   ├── context/          # Bootstrap context loader
│   ├── context_engine.py # ContextEngine Protocol + DefaultContextEngine
│   └── hooks.py          # Plugin hook Protocol
├── channels/             # Channel layer
│   ├── feishu/           # Feishu/Lark (WS receiver, client, commands, streaming, handler)
│   ├── session_router.py # SessionKey → SessionId routing + lazy migration
│   └── web/              # Web channel (planned)
├── storage/              # Storage layer (pluggable backends)
│   ├── session/          # Redis + InMemory session stores
│   ├── workspace/        # File + Redis workspace stores
│   └── lock/             # Redis distributed lock (SET NX PX + Lua CAS)
├── skills/               # Skill Hub (ClawHub compatible)
│   ├── parser.py         # SKILL.md YAML frontmatter + body parser
│   ├── discovery.py      # 5-layer directory scanner + dedup
│   ├── eligibility.py    # Runtime requirement checks (bins, env, OS)
│   ├── prompt.py         # XML prompt injection with budget enforcement
│   ├── clawhub_client.py # ClawHub REST API client (search, download)
│   └── installer.py      # ZIP extraction + lockfile management
├── cli/                  # CLI tools
│   └── skills.py         # pyclaw-skill CLI (list, search, install, check)
├── gateway/              # Multi-instance gateway (planned)
├── infra/                # Redis client, settings, logging
├── models/               # Shared data models (Pydantic)
└── app.py                # FastAPI entry point + lifespan
```

## Quick Start

```bash
# Clone
git clone https://github.com/Timeflys2018/pyclaw.git
cd pyclaw

# Install (Python 3.12+)
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Start (one command — auto-builds frontend, detects Redis)
./scripts/start.sh

# Or manually:
.venv/bin/uvicorn pyclaw.app:create_app --factory --host 0.0.0.0 --port 8000 --reload
```

### Development with Frontend HMR

```bash
./scripts/start.sh           # Terminal 1: backend (port 8000)
./scripts/dev-frontend.sh    # Terminal 2: Vite HMR (port 5173, proxies API to 8000)
```

### Build Frontend Only

```bash
./scripts/build-frontend.sh  # Builds web/dist/ (served by backend)
```

## Configuration

```json
{
  "server": { "host": "0.0.0.0", "port": 8000 },
  "storage": { "session_backend": "redis" },
  "redis": { "host": "localhost", "port": 6379 },
  "agent": {
    "default_model": "anthropic/claude-sonnet-4-20250514",
    "providers": { "anthropic": { "apiKey": "sk-...", "baseURL": "..." } }
  },
  "workspaces": { "default": "~/.pyclaw/workspaces", "backend": "file" },
  "skills": {
    "workspaceSkillsDir": "skills",
    "managedSkillsDir": "~/.openclaw/skills",
    "clawhubBaseUrl": "https://clawhub.ai"
  },
  "channels": {
    "feishu": { "enabled": true, "appId": "cli_...", "appSecret": "..." }
  }
}
```

See `configs/pyclaw.example.json` for all options.

## Deployment Modes

### Development (Zero Dependencies)
```json
{ "storage": { "session_backend": "memory" } }
```
No Redis needed. Sessions in-memory (lost on restart).

### Production (Redis)
```json
{
  "storage": { "session_backend": "redis" },
  "redis": { "host": "your-redis", "port": 6379, "password": "..." }
}
```
Persistent sessions, distributed locks, multi-instance ready.

## Tests

```bash
# Unit tests (no external dependencies)
.venv/bin/pytest tests/ --ignore=tests/e2e

# With real Redis
PYCLAW_TEST_REDIS_HOST=localhost .venv/bin/pytest tests/integration/

# E2E with real LLM
PYCLAW_LLM_API_KEY=sk-... .venv/bin/pytest tests/e2e/
```

599 unit/integration tests, 6 E2E tests with real LLM.

## Security & Isolation

PyClaw is designed as a **personal/small-team assistant**, not a multi-tenant SaaS. Session data, Redis keys, and Feishu workspaces are fully isolated per user. Web channel is designed for trusted users (Tool Approval Hook gates dangerous operations).

See [D26: User Isolation Model](./docs/en/architecture-decisions.md#d26-user-isolation-model--personal-assistant-not-multi-tenant-saas) for full isolation boundaries, known limitations, and multi-tenant upgrade path.

## Documentation

- [Architecture Decisions (D1-D26)](./docs/en/architecture-decisions.md) — all design choices and rationale
- [Session System Design](./docs/en/session-design.md) — SessionKey/SessionId, commands, idle reset
- [Context Engine](./docs/en/context-engine.md) — assemble/ingest/compact Protocol
- [Compaction Guide](./docs/en/compaction-guide.md) — multi-stage context summarization
- [Timeouts & Abort](./docs/en/timeouts-and-abort.md) — run/idle/tool timeout design
- [Skill Hub Compatibility](./docs/en/skill-hub-compatibility.md) — ClawHub integration, SKILL.md format, discovery

Chinese docs: [docs/zh/](./docs/zh/)

## Roadmap

See `openspec/changes/pyclaw-architecture/tasks.md` for the full breakdown. Major remaining items:

- ~~**Web Channel**~~ — ✅ Done: multiplexed WebSocket, OpenAI-compat SSE, React SPA, JWT auth, tool approval
- **Memory Store** — SQLite-vec (dev) + PostgreSQL+pgvector (prod) (roadmap 9.x)
- **Dreaming Engine** — Light/Deep/REM memory consolidation (roadmap 10.x)
- ~~**Skill Hub**~~ — ✅ Done: ClawHub SKILL.md parsing, discovery, installation, CLI
- **Session Affinity Gateway** — multi-instance message routing (when needed)

## Relationship to OpenClaw

PyClaw is inspired by [OpenClaw](https://github.com/openclaw/openclaw) and designed to be compatible with its skill ecosystem. PyClaw is an **independent Python reimplementation**, not a fork. It inherits the domain model (sessions, memory, channels, skills) but redesigns the architecture for compute-storage separation.

## Follow Us

WeChat Official Account: **Time留痕** — PyClaw development journey, AI Agent architecture insights.

<img src="./docs/assets/Time留痕.jpg" width="200" alt="WeChat: Time留痕" />

**Latest Article:** [从 6000 行包装到 645 行单循环：我如何重写 OpenClaw 的 Agent 内核](https://mp.weixin.qq.com/s/sGLHdPsMD1vj8CfUTd6PdQ) — 六大 Agent 框架源码级对比（Claude Code / OpenClaw / OpenCode / DeerFlow / GenericAgent / Hermes）

## Contributing

PRs welcome. See the `openspec/` directory for architectural specs and task breakdown.

## License

[MIT License](./LICENSE) — free to use, modify, and distribute, including commercial use.
