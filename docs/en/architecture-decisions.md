# Architecture Decisions Record

## D1: Python + FastAPI + asyncio

Python 3.12+ with FastAPI (ASGI) + uvicorn as the runtime stack.

**Rationale**: AI/LLM ecosystem is Python-first (litellm, langchain, sentence-transformers). FastAPI gives native async, auto OpenAPI docs, WebSocket support. asyncio is sufficient for I/O-bound workload (LLM calls, Redis ops).

## D2: Redis as primary session store

Redis (Hash + List + Sorted Set) for sessions in production; file backend for development.

> **Note on provenance**: Upstream `openclaw/openclaw` uses **filesystem-based** session storage (JSONL files + fs.FileHandle locks). The Redis-based distributed session layer is a PyClaw-specific design, conceptually inspired by internal forks that adopted Redis but with our own key schema and write-through protocol. Moving sessions to Redis is required for PyClaw's compute-storage separation goal.

**PyClaw Redis key schema** (our own design):
```
session:{id}:header   → String (JSON)
session:{id}:entries  → Hash<entryId, JSON>
session:{id}:order    → List<entryId>
session:{id}:leaf     → String (current head entryId)
session-lock:{id}     → String (lock value, SET NX PX)
session-affinity:{id} → String (instance_id, TTL 5min)
```

## D3: PostgreSQL + pgvector for memory (production)

PG+pgvector for production; SQLite+sqlite-vec for development. Single dependency gives ACID, FTS (tsvector), and vectors.

## D4: Optimistic concurrency + write-time mutex

Reads are lock-free. Writes acquire Redis distributed lock (SET NX PX + Lua CAS release/renew). Lock renewal at TTL/3 intervals.

## D5: ChannelPlugin as Python Protocol with adapter slots

Channels implement only the adapters they need (gateway, outbound, messaging, etc.). New channels added without modifying core.

## D6: ClawHub compatibility via shared directory + REST API

Read skills from `~/.openclaw/skills/` (shared with TypeScript OpenClaw). Native Python httpx ClawHub API client.

## D7: LiteLLM for multi-provider LLM access

Unified interface to 100+ providers. Handles provider-specific format differences that OpenClaw solves with 14-layer stream middleware.

## D8: Independent models/ layer

`src/pyclaw/models/` as shared data model layer. Both `core/` and `storage/` depend on models/ but never on each other.

## D9: AsyncGenerator streaming API

`run_agent_stream()` returns `AsyncGenerator[AgentEvent, None]`. AgentEvent union: TextChunk | ToolCallStart | ToolCallEnd | Done | Error. Phase 2 can add multi-consumer broadcast via asyncio.Queue if needed.

## D10: Write-through session persistence

Every `append_entry()` immediately persists to storage backend. Crash-safe: worker dies mid-run, entries up to that point are visible to other workers.

## D11: Smart-hybrid tool execution

Tools declare `side_effect: bool`. `side_effect=False` (read) execute in parallel via `asyncio.gather`. `side_effect=True` (bash, write, edit) execute sequentially.

## D12: ContextEngine Protocol with default pass-through

Defined in `core/context_engine.py`. Agent runner always calls through it. Phase 1: DefaultContextEngine (pass-through). Phase 2: swap in mem0/langchain implementation, zero runner changes.

## D13: Workspace configuration mapping

`pyclaw.json` contains `workspaces` field mapping workspace_id to filesystem paths. Different machines can map the same workspace_id to different paths.

```json
{
  "workspaces": {
    "default": ".",
    "my-project": "/path/to/project"
  }
}
```

## D14: JSON config format (not YAML)

JSON as primary config format for OpenClaw compatibility. Load order: `pyclaw.json` → `configs/pyclaw.json` → `~/.openclaw/pyclaw.json`. Environment variables override.

## D15: Memory and Dreaming as plugins (not core)

`plugins/memory/` and `plugins/dreaming/` — core has zero dependency on them. Agent runs without memory/dreaming in personal lightweight mode. Injected via hooks + ContextEngine.

## D16: Single-loop agent design

One explicit loop: `assemble_prompt → call_llm → process_response → (tool_calls? execute_tools → loop : done)`. OpenClaw's nested design exists because `session.prompt()` is opaque; we own the full stack.

## D17: Session DAG tree (not flat list)

Sessions are append-only DAG trees. Each entry has id + parent_id. Leaf pointer tracks current head. Compaction creates new branch with summary. `build_session_context()` walks leaf→root to produce flat message list for LLM.

## D18: Single canonical `SessionStore` Protocol

The `SessionStore` Protocol has exactly one definition at `src/pyclaw/storage/session/base.py`. It operates on typed `SessionTree` and `SessionEntry` objects (not raw dicts).

**Why**: An early iteration exposed two conflicting Protocols — a dict-based variant in `storage/protocols.py` and the typed variant in `storage/session/base.py`. Backend implementers had to choose; the runner always used the typed one. The dict variant was dead code that created migration risk.

**Consolidation** (harden-agent-core Group 2):
- `storage/protocols.py` now re-exports the typed `SessionStore` from `session/base.py` — no parallel definition.
- `storage/__init__.py` exports `SessionStore` from the same path.
- All three import paths resolve to the same class:
  - `from pyclaw.storage import SessionStore`
  - `from pyclaw.storage.protocols import SessionStore`
  - `from pyclaw.storage.session.base import SessionStore`

**Protocol surface**:
```python
class SessionStore(Protocol):
    async def load(self, session_id: str) -> SessionTree | None: ...
    async def save_header(self, tree: SessionTree) -> None: ...
    async def append_entry(self, session_id: str, entry: SessionEntry, leaf_id: str) -> None: ...
```

## D19: SessionKey / SessionId two-layer separation

The session system uses two distinct concepts rather than one ID serving multiple roles:

| Concept | Role | Format | Lifetime |
|---|---|---|---|
| **sessionKey** | Routing address, derived from channel context | `feishu:{app_id}:{scope_id}` | Permanently stable |
| **sessionId** | Storage container holding the actual conversation | `{sessionKey}:s:{8hex}` | Rotates on `/new` |

**Background**: Originally `session_id` served as both routing address and storage key (e.g. `feishu:cli_xxx:ou_abc`). This made `/new` impossible to implement — changing the storage key would also change the routing address.

**Design choices**:
- sessionKey stays stable; `pyclaw:skey:{sessionKey}:current` (STRING) points to the active sessionId
- Historical sessionIds archived in `pyclaw:skey:{sessionKey}:history` (ZSET, score = creation time ms)
- `/new` only rotates the sessionId; all Redis keys for the old sessionId are preserved indefinitely
- `SessionRouter` encapsulates routing: sessionKey → sessionId → SessionTree
- Lazy migration: old-format sessions (sessionId == sessionKey) are transparently registered in the new index on first access — zero downtime

**Alternative rejected**: Batch migration script — requires downtime coordination and is risky with many existing sessions.

**Index keys have no TTL**: `skey:current` and `skey:history` persist indefinitely; per-session data keys (header/entries/order/leaf) retain sliding TTL (default 30 days).

**Related**: `implement-session-key-rotation` change, `src/pyclaw/channels/session_router.py`

## D20: Command interception at the channel layer; agent layer is unaware

Feishu commands (`/new`, `/status`, `/whoami`, etc.) are intercepted in `handle_feishu_message()` and handled directly by command handlers — the **agent runner is never invoked**.

**Rationale**:
- Commands complete in milliseconds; no LLM reasoning needed
- Keeps agent runner single-purpose (conversation only)
- Command replies use plain text — no `cardkit:card:write` permission required
- Adding new commands only requires channel-layer changes; core is untouched

**Command set** (essential tier): `/new`, `/reset`, `/status`, `/whoami`, `/history`, `/help`, `/idle <Xm>`

Unrecognized `/`-prefixed messages pass through to the agent as normal user messages.

**Related**: `src/pyclaw/channels/feishu/commands.py`

## D21: Idle auto-reset via last_interaction_at tracking

`SessionHeader` carries `last_interaction_at: str | None` (UTC ISO timestamp), updated after every user message is processed by the agent runner. System events (command replies, heartbeats) do not update this field.

**idle_minutes** configuration precedence (high to low):
1. Per-session override set via `/idle 30m` (stored in `SessionHeader.idle_minutes_override`)
2. `FeishuSettings.idle_minutes` (global default, default 0 = disabled)

**Default off**: Consistent with OpenClaw (`DEFAULT_IDLE_MINUTES = 0`). When enabled, a message arriving after the idle window silently triggers session rotation; both old and new sessions are fully preserved.
