# Upstream OpenClaw Compaction Subsystem — Audit Reference

Source: `github.com/openclaw/openclaw` HEAD `388019f5b6` (synced at `~/CascadeProjects/selfLearning/openclaw`). This document is the reference for PyClaw's `harden-agent-core` change.

## Scope

Upstream has ~22 files dedicated to compaction across `src/agents/pi-embedded-runner/` and `src/agents/`. PyClaw currently has a single `compaction.py` file with a naive cut-point + LLM-summary implementation. This document lists every upstream concept that PyClaw should evaluate.

## Lifecycle Overview

```
TRIGGER (budget / overflow / manual)
  ↓
compactEmbeddedPiSession() ── enqueues into session lane + global lane
  ↓
ContextEngine owns compaction? ──YES──> contextEngine.compact() + maintenance
  ↓ NO
compactEmbeddedPiSessionDirect()
  ├─ Acquire session write lock
  ├─ Sanitize: sanitize → validate → dedupe → limit → repair pairing
  ├─ Build hook metrics (original + current counts)
  ├─ Fire before_compaction hooks
  ├─ Guard: skip if no real conversation
  ├─ compactWithSafetyTimeout(session.compact(), 15min, {abortSignal, onCancel})
  ├─ If manual: hardenManualCompactionBoundary()
  ├─ Maybe rotateTranscriptAfterCompaction() (successor file)
  ├─ Persist checkpoint (rollback snapshot)
  ├─ Fire after_compaction hooks
  └─ runPostCompactionSideEffects (transcript update event + memory sync)
```

## File Inventory (upstream)

| File | Purpose | LoC |
|------|---------|-----|
| `compact.ts` | Main direct compaction runtime | 1273 |
| `compact.types.ts` | Parameter + metrics types | 83 |
| `compact.queued.ts` | Queued entry point with lane queueing + ContextEngine delegation | 334 |
| `compact.runtime.ts` | Lazy loader proxy | 15 |
| `compact.hooks.test.ts` | Hook lifecycle tests | 1122 |
| `compaction-duplicate-user-messages.ts` | 60s-window duplicate detection | 109 |
| `compaction-hooks.ts` | before/after hook orchestration + side effects | 308 |
| `compaction-runtime-context.ts` | Runtime context + compaction target resolution | 127 |
| `compaction-safety-timeout.ts` | 15-min timeout wrapper + abort integration | 93 |
| `compaction-successor-transcript.ts` | Post-compaction transcript rotation | 282 |
| `compact-reasons.ts` | Reason code classification (10+ categories) | 76 |
| `empty-assistant-turn.ts` | Zero-token empty turn detection | 57 |
| `context-engine-maintenance.ts` | Post-compaction + post-turn maintenance | 651 |
| `manual-compaction-boundary.ts` | /compact manual boundary hardening | 117 |
| `src/agents/compaction.ts` | Shared summarization algorithms | 579 |
| `src/agents/compaction-real-conversation.ts` | "Real conversation" heuristic | 85 |
| `run/compaction-timeout.ts` | Run-level timeout grace | 72 |
| `run/compaction-retry-aggregate-timeout.ts` | Retry aggregate timeout | 59 |
| `pi-hooks/compaction-safeguard-runtime.ts` | Safeguard state store | 75 |

## Key Algorithms

### Multi-Stage Summarization (`compaction.ts`)
- `BASE_CHUNK_RATIO = 0.4`, `MIN_CHUNK_RATIO = 0.15`
- `SAFETY_MARGIN = 1.2` applied to all token estimates
- `SUMMARIZATION_OVERHEAD_TOKENS = 4096` reserved for prompt overhead
- `summarizeInStages`: split messages by token share → summarize each chunk → merge summaries
- `summarizeWithFallback`: full → partial (exclude oversized) → note-only
- `splitMessagesByTokenShare`: preserves tool_use/tool_result pairing when splitting
- `pruneHistoryForContextShare`: drops oldest chunks until under budget, then repairs pairing

### Identifier Preservation Policies
Three modes for `customInstructions`:
- `"strict"` (default) — preserve UUIDs, hashes, IDs, hostnames, IPs, ports, URLs, filenames
- `"custom"` — user-provided instructions
- `"off"` — no special handling

### Duplicate User Message Dedup
- Window: 60s default (configurable)
- Min length: 24 chars (short acks never deduped)
- Normalization: collapse whitespace → NFC → lowercase
- Key: normalized text → `lastSeenAt` timestamp

### Safety Timeout
- Default: 900,000 ms (15 min)
- Config: `agents.defaults.compaction.timeoutSeconds`
- Races: user compaction fn vs timeout signal vs external abort signal
- On fire: calls `onCancel` → `session.abortCompaction()` — idempotent

### Successor Transcript Rotation
Triggered by `agents.defaults.compaction.truncateAfterCompaction: true`:
1. Find latest compaction entry; mark pre-`firstKeptEntryId` entries as "summarized"
2. Keep latest state entries (model_change, thinking_level_change, session_info); drop stale ones
3. Remove duplicate user messages (entry-level)
4. Re-parent surviving entries (patch parentId chains)
5. Write atomically to successor file (temp + rename)
6. Validate via `buildSessionContext()` — delete if invalid
7. Old file preserved as archive (linked via `parentSession`)

### Reason Codes
- `no_compactable_entries`
- `below_threshold`
- `already_compacted_recently`
- `live_context_still_exceeds_target`
- `guard_blocked`
- `summary_failed`
- `timeout`
- `provider_error_4xx` (400/401/403/429)
- `provider_error_5xx` (500/502/503/504)
- `unknown`

### Hooks

Internal events:
- `session:compact:before` / `session:compact:after`

Plugin hooks:
- `before_compaction` — receives `{messageCount, tokenCount}` + runtime context
- `after_compaction` — receives `{messageCount, tokenCount, compactedCount, sessionFile}` + context
- `onSessionTranscriptUpdate` — subscribers refresh views

Hook exceptions never abort compaction.

### Post-Compaction Memory Sync
Three modes (`agents.defaults.memorySearch.sync.postCompactionMode`):
- `"off"` — skip sync
- `"async"` — fire-and-forget
- `"await"` — block until done
Requires `postCompactionForce: true` to actually run.

### Runtime Context (Non-Obvious Fields)

Beyond messages, compaction needs:
- Routing identity: sessionKey, channel, thread, message IDs, sender info
- Auth: compaction model may differ from chat model (config override)
- Skills snapshot: frozen skill state for consistency during compaction
- Sandbox / workspace resolution
- Diagnostic IDs: diagId, runId
- Trigger metadata: "budget" | "overflow" | "manual", force, attempt, maxAttempts
- External AbortSignal

## What Does NOT Translate to PyClaw

| Upstream Feature | Why It Doesn't Translate |
|------------------|--------------------------|
| pi-coding-agent DAG session (entries with parentId) | PyClaw uses flat message list in Redis/file |
| CommandLane global/session queueing | Replaced by Redis distributed lock scoping |
| `session.compact()` / `session.abortCompaction()` | PyClaw must implement LLM summarization + abort itself |
| Agent harness delegation | PyClaw doesn't have agent harnesses |
| MCP/LSP tool runtimes during compaction | Compaction doesn't need live tools |
| Successor transcript (file-based) | PyClaw: equivalent is Redis key rotation / entry pruning |

## Portable Concepts (PyClaw Should Adopt)

Prioritized for `harden-agent-core` implementation:

### P0 — Correctness & Reliability
1. Safety timeout (15 min default, configurable, AbortSignal integrated)
2. Duplicate user message dedup (60s window, NFC-normalized)
3. Token estimation sanity check (`tokens_after > tokens_before` → null)
4. Strip `toolResult.details` before summarization (security)
5. Real conversation guard (skip if only heartbeats)

### P1 — Quality
6. Multi-stage summarization (split → summarize parts → merge)
7. Identifier preservation instructions
8. Oversized message fallback (>50% context → exclude + note)
9. Adaptive chunk ratio for large messages
10. Tool use/result pairing repair after truncation

### P2 — Extensibility
11. Before/after hooks with exception isolation
12. Reason code classification
13. Compaction model override (config)
14. Post-compaction memory sync (await/async/off)
15. Checkpoint snapshot for rollback

### P3 — Distributed Operation
16. Queued concurrency (Redis-lock-scoped per session + system semaphore)
17. Run-level timeout grace period
18. Manual compaction boundary hardening
19. Transcript rotation equivalent (Redis entry GC)
