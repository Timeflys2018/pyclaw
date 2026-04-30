# PyClaw

[中文文档](./README_CN.md)

A Python reimplementation of [OpenClaw](https://github.com/openclaw/openclaw), built from the ground up for **compute-storage separation**, **horizontal scaling**, and **modular architecture**.

## Why PyClaw?

OpenClaw is a powerful multi-channel AI assistant — but its TypeScript monolith (17,000+ files) tightly couples compute and storage, making it hard to scale beyond a single machine. PyClaw takes the best ideas from OpenClaw and rebuilds them with a production-first architecture:

**Compute-Storage Separation** — The core layer is stateless. Sessions, memory, dreaming state — all live in shared storage (Redis, PostgreSQL). Spin up N instances behind a load balancer and they just work.

**Horizontal Scaling** — Session affinity routing, distributed write locks (Redis), leader election for background tasks. No single point of compute failure.

**Modular by Design** — Every layer is a Python Protocol. Swap Redis for files in development. Swap SQLite for PostgreSQL+pgvector in production. Add a new channel without touching core.

**Enterprise & Personal** — Same codebase scales from a single-process dev setup (zero dependencies beyond Python) to multi-instance production with Redis Cluster and PostgreSQL HA.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Compute Layer (Stateless)                                       │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐                       │
│  │ Worker 1 │  │ Worker 2 │  │ Worker N │  ← Scale horizontally │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘                       │
└───────┼──────────────┼─────────────┼─────────────────────────────┘
        │              │             │
        ▼              ▼             ▼
┌─────────────────────────────────────────────────────────────────┐
│  Storage Layer (Shared)                                          │
│  ┌─────────┐  ┌──────────────┐  ┌──────────┐  ┌─────────────┐  │
│  │  Redis  │  │  PostgreSQL  │  │  Memory  │  │   Config    │  │
│  │Sessions │  │  + pgvector  │  │  Store   │  │   Store     │  │
│  │ + Locks │  │  (vectors)   │  │          │  │             │  │
│  └─────────┘  └──────────────┘  └──────────┘  └─────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## Key Features

| Feature | Description |
|---------|-------------|
| **Compute-Storage Separation** | Stateless workers + shared storage = true horizontal scaling |
| **Pluggable Backends** | Redis/File for sessions, PG/SQLite for memory, Redis/flock for locks |
| **Multi-Channel** | Feishu (Lark) + Web (HTTP/WebSocket), extensible via Protocol |
| **ClawHub Compatible** | Uses the same [ClawHub](https://clawhub.ai) skill ecosystem (13,000+ skills) |
| **Dreaming Engine** | Three-phase memory consolidation (light/deep/REM) with distributed scheduling |
| **Multi-Provider LLM** | OpenAI, Anthropic, Google, Ollama, and 100+ providers via LiteLLM |
| **Session Affinity** | Sticky routing with automatic failover on instance crash |
| **Distributed Locking** | Redis SET NX PX + Lua CAS — battle-tested concurrency control |

## Project Structure

```
src/pyclaw/
├── core/                 # Compute layer (stateless, minimal)
│   ├── agent/            # LLM loop, tools, system prompt, compaction
│   └── hooks.py          # Plugin hook Protocol (extensibility seam)
├── plugins/              # Optional capabilities (zero core dependency)
│   ├── memory/           # Memory plugin — embedding, chunking, hybrid search
│   └── dreaming/         # Dreaming plugin — light/deep/REM memory consolidation
├── storage/              # Storage layer (pluggable backends)
│   ├── protocols.py      # Protocol interfaces (swap backends freely)
│   ├── session/          # Redis + File backends
│   ├── memory/           # PostgreSQL + SQLite backends
│   └── lock/             # Redis + File backends
├── channels/             # Channel layer (extensible)
│   ├── feishu/           # Feishu/Lark webhook + API
│   └── web/              # HTTP API + WebSocket
├── skills/               # ClawHub compatibility
│   ├── parser.py         # SKILL.md frontmatter parsing
│   ├── discovery.py      # Local skill scanning
│   └── clawhub_client.py # ClawHub REST API
├── infra/                # Redis client, config, logging
└── orchestration/        # Health checks, instance lifecycle
```

## Quick Start

```bash
# Clone
git clone https://github.com/Timeflys2018/pyclaw.git
cd pyclaw

# Install (Python 3.12+)
pip install -e ".[dev]"

# Run (development mode — no Redis/PG needed)
pyclaw
```

## Deployment Modes

### Personal (Single Machine)
```yaml
storage:
  session_backend: file
  memory_backend: sqlite
  lock_backend: file
```
Zero external dependencies. Just Python.

### Enterprise (Multi-Instance)
```yaml
storage:
  session_backend: redis
  memory_backend: postgres
  lock_backend: redis
```
N workers behind a load balancer. Redis for hot state. PostgreSQL for durable memory.

## Skill Hub Compatibility

PyClaw is fully compatible with OpenClaw's [ClawHub](https://clawhub.ai) skill ecosystem:

- Reads the same `SKILL.md` format (YAML frontmatter + Markdown instructions)
- Shares skill directory with OpenClaw (`~/.openclaw/skills/`)
- Same ClawHub REST API client (search, install, update)
- Skills installed by either tool are available to both

## Relationship to OpenClaw

PyClaw is inspired by [OpenClaw](https://github.com/openclaw/openclaw) and designed to be compatible with its [ClawHub](https://clawhub.ai) skill ecosystem. PyClaw is an **independent Python reimplementation**, not a fork. It inherits the domain model (sessions, memory, dreaming, channels, skills) but redesigns the architecture for compute-storage separation and horizontal scaling.

## Contributing

PRs welcome. See the `openspec/` directory for architectural specs and task breakdown.

## License

MIT
