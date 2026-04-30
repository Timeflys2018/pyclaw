# OpenClaw Architecture Analysis

Analysis of [OpenClaw](https://github.com/openclaw/openclaw) for PyClaw reimplementation reference.

> **Source of truth**: This document describes **upstream `openclaw/openclaw`** (HEAD: `388019f5b6`).
> Sections explicitly marked *"local-fork observation"* refer to the internal fork at `git.n.local.com:mit/ai_center/openclaw` which adds Redis-native session storage; these are *not* upstream behavior.

## Project Overview

- TypeScript monorepo, 17,200+ files, 133 extensions
- Multi-channel AI assistant gateway (25+ messaging platforms)
- Built on `@mariozechner/pi-coding-agent` + `@mariozechner/pi-agent-core` (proprietary libraries)
- MIT license, 357K+ stars

## Core Architecture (upstream)

```
Gateway (HTTP/WS, port 18789)
├── Channels (25+ messaging platforms via plugin registry)
├── Agent Runtime (pi-embedded-runner + run/)
│   ├── Outer loop (run.ts): retry, failover, compaction orchestration
│   └── Inner loop (run/attempt.ts): session.prompt() → LLM → tools → loop
├── Session Storage (FILE-SYSTEM — JSONL files + fs.FileHandle locks)
├── Context Engine (src/context-engine/ — pluggable assemble/ingest/compact/maintain)
├── Memory System (SQLite + embeddings, as extension/plugin)
├── Dreaming (3-phase memory consolidation: light/deep/REM)
├── Skills (ClawHub ecosystem, SKILL.md format)
└── Config (~/.openclaw/openclaw.json)
```

## Session Management (upstream)

### Data Model (DAG Tree)
- SessionHeader: version=3, id (UUID), cwd (filesystem path), timestamp
- SessionEntry subtypes: message, compaction, branch_summary, thinking_level_change, model_change, custom, label
- Each entry has id (8-char hex) and parent_id
- Leaf pointer tracks current conversation head
- Entries are NEVER modified or deleted — tree only grows

### Storage (upstream = filesystem)
- Sessions stored as **JSONL files** on disk
- Session path derived from `cwd` + agent workspace convention
- Write lock: `src/agents/session-write-lock.ts` uses **fs.FileHandle** + **PID detection via `/proc/pid/stat`** for stale locks — purely local

### cwd Coupling (must redesign for PyClaw)
- `cwd` in SessionHeader = agent workspace directory (NOT process.cwd)
- pi-coding-agent's SessionManager is tightly coupled to a filesystem JSONL path
- PyClaw replaces with `workspace_id` (logical identifier) + its own in-memory DAG + pluggable storage backend

---

### local-fork observation: Redis-native session storage

The local internal fork (`git.n.local.com:mit/ai_center/openclaw`) adds a parallel Redis-backed session layer **not present upstream**:

```
session:{id}:header   → String (JSON)
session:{id}:entries  → Hash<entryId, JSON>
session:{id}:order    → List (append order)
session:{id}:leaf     → String (leaf entryId)
session-lock:{id}     → String (distributed lock)
```

Key files (local-fork only, not upstream):
- `redis-session-adapter.ts` — adapter wrapping pi-coding-agent with Redis reads/writes
- `redis-session-keys.ts` — key schema helpers
- `open-redis-session.ts` — session open entry point
- `session-write-lock-redis.ts` — Redis `SET NX PX` + Lua CAS release, TTL/3 renewal, reentrant detection by instance_id prefix

The adapter still creates a tmpfile to feed pi-coding-agent's filesystem-bound SessionManager.

**Relevance to PyClaw**: PyClaw takes the *idea* (Redis-backed sessions for compute-storage separation) but designs its own key schema, avoids the tmpfile hack, and owns the full stack (no pi-coding-agent dependency).

## Agent Loop (pi-embedded-runner)

### Two Nested Loops
1. **Outer** (`run.ts`): while(true) with retry/failover/compaction. Max iterations: 32-160.
2. **Inner** (inside `session.prompt()`): LLM call → tool exec → loop until no tool_calls.

### Stream Function Chain (14 layers)
Most handle multi-provider format differences (litellm eliminates need for these):
1. Provider stream override
2. WebSocket transport
3. Text transforms
4. LLM call diagnostics ← **keep**
5. Drop thinking blocks
6. Sanitize tool call IDs
7. Yield abort guard
8. Sanitize malformed tool calls ← **keep**
9. Trim unknown tool names ← **keep**
10. Repair tool call arguments
11. Decode HTML entities (xAI)
12. Anthropic payload logging
13. Sensitive stop reason recovery
14. Idle timeout ← **keep**

PyClaw keeps 3 layers (diagnostics, sanitize, idle timeout). litellm handles the rest.

### System Prompt Assembly (30+ sections)
Key sections in order:
1. Identity line
2. Tooling (available tools)
3. Skills (`<available_skills>` XML)
4. Safety rules
5. Memory (via plugin hook)
6. Workspace context
7. Bootstrap files (AGENTS.md, etc. — 12K per file, 60K total budget)
8. Runtime info (model, timestamp, agent)
9. Cache boundary marker (for Anthropic prompt caching)

### Tools (complete inventory)
Base: read, write, edit, grep, find, ls, exec (bash), process
OpenClaw additions: canvas, nodes, cron, message, tts, image_generate, web_search, web_fetch, sessions_spawn, subagents, etc.

PyClaw Phase 1: bash, read, write, edit (4 tools).

## Context Engine

Pluggable interface between agent loop and context management strategy.

### Interface
```
bootstrap() → initialize engine state
assemble()  → assemble model context under token budget
ingest()    → capture messages into engine store
compact()   → reduce context token usage
afterTurn() → post-turn lifecycle work
maintain()  → transcript maintenance
```

### Default (LegacyContextEngine)
- assemble: pass-through
- ingest: no-op
- compact: delegates to runtime compaction
- afterTurn: no-op

Third-party engines can implement RAG injection, custom compaction, etc.

## Memory System

Lives in `extensions/memory-core/` (NOT in core — it's a plugin).

### Storage
- SQLite + sqlite-vec (vector) + FTS5 (full-text)
- Path: `{workspace}/.memory/index.sqlite`
- Sources: MEMORY.md, memory/*.md, session transcripts

### Hooks
- `before_prompt_build` → auto-recall relevant memories
- `llm_output` → auto-capture important information

### Hybrid Search
- Vector similarity (cosine, weight 0.7) + FTS (BM25, weight 0.3)
- Temporal decay, MMR diversity, configurable thresholds

## Dreaming System

Background memory consolidation (cron-scheduled):
- **Light**: Every 6h. Deduplication + candidate staging.
- **Deep**: Daily 3 AM. LLM-powered promotion to long-term memory.
- **REM**: Weekly. Cross-memory pattern discovery.

State stored in `memory/.dreams/` (filesystem — must redesign for PyClaw).

## Skill System

### Format
```
skills/{name}/SKILL.md
```
YAML frontmatter (name, description, metadata.openclaw) + Markdown body (injected into agent system prompt).

### ClawHub Registry (https://clawhub.ai)
- 13,000+ skills, MIT-0 license
- REST API: `/api/v1/skills`, `/api/v1/search`, `/api/v1/download`
- Download format: ZIP archive
- Auth: Bearer token (env var or ~/.config/clawhub/config.json)
- Install to: `{workspace}/skills/{slug}/SKILL.md`
- Lockfile: `.clawhub/lock.json`

### Discovery Order (high → low priority)
1. Workspace skills (`{workspace}/skills/`)
2. Project agent skills (`{workspace}/.agents/skills/`)
3. Personal agent skills (`~/.agents/skills/`)
4. Managed skills (`~/.openclaw/skills/`)
5. Bundled skills (shipped with binary)
6. Extra dirs + plugin skills

### Prompt Budget
- Max 150 skills in prompt
- Max 18,000 chars total
- Max 256KB per SKILL.md
- Fallback to compact format (no descriptions) if over budget

## Channel System

### ChannelPlugin Interface (~30 adapter slots)
Required: id, meta, config
Optional: gateway, outbound, security, messaging, threading, directory, streaming, lifecycle, etc.

### Message Flow
```
Platform webhook → Channel Monitor → InboundMessage normalization
  → dispatchInboundMessage() → Agent processing
  → ReplyPayload → Channel outbound adapter → Platform API
```

### Feishu Implementation
- Plugin manifest: `openclaw.plugin.json`
- Webhook/long-polling for inbound
- Feishu Open API for outbound
- Supports DM + group @mention
