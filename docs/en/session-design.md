# Session System Design

> This document captures the complete design of PyClaw's session system, including the OpenClaw comparison analysis, key decisions, and implementation path.

## 1. Root Problem

The original `session_id` (e.g. `feishu:cli_xxx:ou_abc`) served three roles simultaneously:

```
feishu:cli_xxx:ou_abc
  ↑ Routing address (should be permanently stable)
  ↑ Redis storage key prefix (should be rotatable)
  ↑ Workspace path derivation source (colon → underscore)
```

These three roles conflict. When a user sends `/new`, there is no way to create a fresh conversation without breaking the routing address.

## 2. OpenClaw's Two-Layer Design

Investigation of the OpenClaw TypeScript implementation revealed it solves this with two distinct concepts:

```
sessionKey  "agent:main:telegram:direct:ou_xxx"
                ← Routing address, permanently stable
                ← Format: agent:{agentId}:{rest}
                ← Lives in: sessions:{agentId} Hash (index)
    │
    └── sessionId  "550e8400-e29b-41d4-a716-446655440000"
                    ← UUID, storage container, rotates on /new
                    ← Lives in: session:{sessionId}:* Redis keys
```

`/new` only rotates the sessionId; sessionKey stays the same. Old conversation is archived.

## 3. PyClaw's Adaptation

PyClaw adopts the same layering with Python-native formatting:

### 3.1 SessionKey Format

```python
def build_session_key(app_id: str, event, scope: str) -> str:
    # DM
    if chat_type == "p2p":
        return f"feishu:{app_id}:{open_id}"
    # Group per-user isolation
    if scope == "user":
        return f"feishu:{app_id}:{chat_id}:{open_id}"
    # Thread
    if scope == "thread" and thread_id:
        return f"feishu:{app_id}:{chat_id}:thread:{thread_id}"
    # Group shared (default)
    return f"feishu:{app_id}:{chat_id}"
```

This is identical logic to the original `build_session_id()` — only the semantic role changes.

### 3.2 SessionId Format

```
{sessionKey}:s:{8-char-hex}

Example: feishu:cli_xxx:ou_abc:s:a1b2c3d4
```

- 8-char hex = 32-bit space, ~4 billion combinations, sufficient for collision resistance
- Retains sessionKey prefix for log readability and SCAN queries
- `:s:` separator distinguishes new-format sessions from migrated legacy sessions

### 3.3 Complete Redis Key Schema

```
┌─────────────────────────────────────────────────────────────────────┐
│  Per-Session (keyed by sessionId, sliding TTL)                      │
│  pyclaw:session:{<sessionId>}:header    STRING  SessionHeader JSON  │
│  pyclaw:session:{<sessionId>}:entries   HASH    message content     │
│  pyclaw:session:{<sessionId>}:order     LIST    message order       │
│  pyclaw:session:{<sessionId>}:leaf      STRING  current leaf        │
│  session-lock:{<sessionId>}             STRING  write lock (30s)    │
├─────────────────────────────────────────────────────────────────────┤
│  Per-SessionKey (keyed by sessionKey, NO TTL, permanent)            │
│  pyclaw:skey:{<sessionKey>}:current     STRING  active sessionId   │
│  pyclaw:skey:{<sessionKey>}:history     ZSET    all sessionIds     │
│                                                 score = created ms  │
└─────────────────────────────────────────────────────────────────────┘
```

**Why skey keys have no TTL**: The value of the history index is completeness. Session data may expire, but the index (pointing to expired data) remains. `/history` shows "archived" status.

## 4. SessionRouter

`SessionRouter` is the core of the routing layer, encapsulating the sessionKey → sessionId → SessionTree resolution:

```python
@dataclass
class SessionRouter:
    store: SessionStore

    async def resolve_or_create(session_key, workspace_id, agent_id="default"):
        # 1. Check new-format skey:current
        session_id = await store.get_current_session_id(session_key)
        if session_id:
            tree = await store.load(session_id)
            if tree: return (session_id, tree)

        # 2. Lazy migration: check old format (session_id == session_key)
        old_tree = await store.load(session_key)
        if old_tree:
            await store.set_current_session_id(session_key, session_key)
            return (session_key, old_tree)

        # 3. Create new session
        tree = await store.create_new_session(session_key, workspace_id, agent_id)
        return (tree.header.id, tree)

    async def rotate(session_key, workspace_id, agent_id="default"):
        old_id = await store.get_current_session_id(session_key)
        tree = await store.create_new_session(
            session_key, workspace_id, agent_id,
            parent_session_id=old_id
        )
        return (tree.header.id, tree)
```

**Lazy migration** requires no downtime and no batch scripts. Each user migrates transparently on their next message.

## 5. Command System

### 5.1 Architectural Principle

Commands are intercepted at the **channel layer** — the agent runner is never invoked:

```
Feishu message arrives
    ↓
handle_feishu_message()
    ↓
is_command(text)?  ─── yes ──→ commands.py handles → direct reply → done
    ↓ no
SessionRouter.resolve_or_create()
    ↓
idle check (if expired → rotate())
    ↓
dispatch_message() → agent runner → LLM
```

**Rationale**: Commands complete in milliseconds; no LLM needed. Command replies use plain text — no `cardkit:card:write` permission required. Agent runner stays single-purpose.

### 5.2 Full Command List

| Command | Behavior | Notes |
|---|---|---|
| `/new` | Create new sessionId, archive old conversation | Supports `/new <initial message>` |
| `/reset` | Same as `/new`, different reply wording | |
| `/status` | Show sessionKey, sessionId, message count, model, created_at | |
| `/whoami` | Show open_id, chat_type, chat_id | |
| `/history` | List up to 10 past sessions for this sessionKey | |
| `/help` | Show all command descriptions | |
| `/idle <Xm\|Xh\|off>` | Set idle auto-reset threshold | Per-session override of global setting |

Unrecognized `/`-prefixed messages pass through to the agent.

### 5.3 /new Complete Flow

```
User: "/new"
    ↓
1. is_command("/new") → True
2. old_id = get_current_session_id(session_key)
3. new_tree = create_new_session(
       session_key, workspace_id,
       parent_session_id=old_id    ← chains history
   )
4. reply_text("✨ New session started. Previous conversation archived.")
   (direct reply, no agent)
5. done

User: "/new write a fibonacci function"
    ↓
1-4. same as above
5. dispatch_message("write a fibonacci function", new_session_id)
   → agent processes
```

## 6. Idle Auto-Reset

### 6.1 Mechanism

`SessionHeader.last_interaction_at` records the last user message timestamp. Updated after each message is processed by the agent runner; system events do not update it.

```
Message arrives
    ↓
now - last_interaction_at > idle_minutes × 60?
    yes → rotate() silently (no notification to user)
    no  → process normally
    ↓
agent finishes → update_last_interaction(session_id)
```

### 6.2 Configuration Precedence

```
Per-session /idle 30m  (highest priority, stored in SessionHeader.idle_minutes_override)
    ↓ overrides
FeishuSettings.idle_minutes  (global default, 0 = disabled)
```

Default off (`idle_minutes = 0`), consistent with OpenClaw.

## 7. OpenClaw Comparison

| Dimension | OpenClaw | PyClaw |
|---|---|---|
| Routing key format | `agent:main:telegram:direct:ou_xxx` | `feishu:cli_xxx:ou_abc` |
| Storage container format | UUID | `{sessionKey}:s:{8hex}` |
| Session index | `sessions:{agentId}` Hash + ZSet | `skey:{sessionKey}:current` + history |
| /new behavior | Rotate sessionId, archive transcript, trigger memory hook | Rotate sessionId, archive Redis keys |
| TTL | None (permanent) | Sliding 30 days (index keys have no TTL) |
| Command system | 40+ commands including /subagents /acp /tts | 7 essential commands |
| parent_session | Thread fork, subagent, dashboard sessions | /new history chain |
| Idle reset | daily (4AM) + idle dual mode, per-channel config | idle mode, global + per-session config |

## 8. Future Evolution

- **Thread session parent fork**: On thread session creation, seed from parent group session's last N messages as starting context (OpenClaw behavior, 100K token safety limit)
- **Web channel commands**: Equivalent command endpoints on HTTP API
- **Session export**: `/export` to export current conversation as HTML or JSONL
- **Memory integration**: `/new` triggers memory hook to extract summary into long-term memory (dreaming engine prerequisite)

---

*Related change*: `openspec/changes/implement-session-key-rotation/`  
*Related decisions*: D19 (SessionKey/SessionId separation), D20 (command interception), D21 (idle reset)
