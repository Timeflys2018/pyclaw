# Context Engine Design

## What It Is

The Context Engine is a pluggable interface between the agent loop and context management strategy. It solves: **how to fit the most valuable information into a limited context window**.

## OpenClaw's Implementation

OpenClaw defines `ContextEngine` as a plugin interface with lifecycle methods:

```
bootstrap() → initialize engine state for a session
assemble()  → assemble model context under token budget (can inject/rewrite messages)
ingest()    → capture new messages into engine's internal store
compact()   → reduce token usage (summarize, prune)
afterTurn() → post-turn lifecycle (persist state, trigger background compaction)
maintain()  → transcript maintenance (rewrite entries for safety/efficiency)
```

Default `LegacyContextEngine` is a no-op pass-through — real logic is in the agent runner's sanitize/validate/limit pipeline. The interface exists for third-party plugins (RAG, custom memory systems).

## PyClaw's Approach

### Phase 1: Protocol + DefaultContextEngine

```python
class ContextEngine(Protocol):
    async def assemble(self, messages, token_budget, prompt) -> AssembleResult: ...
    async def ingest(self, session_id, message) -> None: ...
    async def compact(self, session_id, messages, token_budget, force) -> CompactResult: ...
    async def after_turn(self, session_id, messages) -> None: ...

@dataclass
class AssembleResult:
    messages: list[dict]
    system_prompt_addition: str | None = None
```

`DefaultContextEngine`:
- `assemble()` → pass-through (return messages unchanged)
- `ingest()` → no-op
- `compact()` → delegate to built-in compaction (find_cut_point + LLM summarize)
- `after_turn()` → no-op

### Phase 2: Third-Party Integration

Swap implementation without changing agent runner:

```python
class Mem0ContextEngine:
    async def assemble(self, messages, token_budget, prompt):
        # Query mem0 for relevant memories
        memories = await self.mem0.search(prompt, limit=5)
        # Inject as system prompt addition
        return AssembleResult(
            messages=messages,
            system_prompt_addition=format_memories(memories)
        )

    async def ingest(self, session_id, message):
        # Capture conversation to mem0
        await self.mem0.add(message.content, user_id=session_id)
```

### Why Pre-define the Interface

Agent runner calls through ContextEngine from day one:
```python
assembled = await context_engine.assemble(messages, token_budget, prompt)
# ... LLM call ...
await context_engine.ingest(session_id, response_message)
# ... after tool loop ends ...
await context_engine.after_turn(session_id, all_messages)
```

Phase 2 swaps engine implementation — runner code untouched. Without pre-defining, Phase 2 would modify runner at 4 call sites with regression risk.

### Relationship to Hooks

| Mechanism | Scope | Use Case |
|-----------|-------|----------|
| AgentHook (before_prompt_build) | Lightweight — append to system prompt | Simple memory recall, skill injection |
| ContextEngine | Heavyweight — rewrite messages, own compaction | Full RAG pipeline, mem0, langchain memory |

Both coexist. Hooks for simple plugins. ContextEngine for systems that need deep control over context assembly.
