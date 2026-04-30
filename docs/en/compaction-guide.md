# Compaction Guide

Compaction is PyClaw's mechanism for keeping conversations within the LLM's context window. When a session grows large, the agent compresses older messages into a summary, keeping the most recent exchanges intact.

## When Compaction Triggers

Compaction runs when estimated token usage exceeds the threshold:

```
estimated_tokens > context_window * compaction.threshold
```

Default threshold is 0.8 (80%). The estimate applies a 1.2× safety margin.

## Configuration

All settings live under `agent.compaction` in `pyclaw.json`:

```json
{
  "agent": {
    "compaction": {
      "model": null,
      "threshold": 0.8,
      "keep_recent_tokens": 20000,
      "timeout_seconds": 900.0,
      "truncate_after_compaction": false
    }
  }
}
```

| Key | Default | Description |
|---|---|---|
| `model` | `null` | Override LLM model used for summarization. Defaults to the chat model. |
| `threshold` | `0.8` | Fraction of `context_window` that triggers compaction. |
| `keep_recent_tokens` | `20000` | Minimum tokens of recent messages preserved uncompacted. |
| `timeout_seconds` | `900.0` | Safety timeout for a compaction attempt (15 minutes). |
| `truncate_after_compaction` | `false` | If true, hard-truncate residual messages if still over budget. |

### Using a Cheaper Model for Compaction

Summarization is a well-bounded task that rarely benefits from the most expensive model:

```json
{
  "agent": {
    "default_model": "anthropic/claude-opus-4",
    "compaction": { "model": "openai/gpt-4o-mini" }
  }
}
```

## What Gets Preserved

The summarizer prompt explicitly instructs the LLM to preserve identifiers verbatim:
- UUIDs, hashes, IDs
- Hostnames, IP addresses, ports, URLs
- Filenames and paths
- Model names, session IDs, commit SHAs, error codes

## Behaviors

### Duplicate User-Message Dedup
Before summarization, consecutive duplicate user messages (normalized via NFC + whitespace-collapse + lowercase) within a 60-second window are deduplicated. Messages shorter than 24 characters are never deduped. This handles double-sends without losing short acknowledgements like "ok".

### Real-Conversation Guard
Compaction is skipped entirely if the session contains only non-conversational entries (heartbeats, system notices). Prevents wasteful summarizer calls on idle sessions.

### Oversized-Message Fallback
Any single message exceeding 50% of the context window is excluded from summarization and replaced with `[omitted oversized message from {role}]`. Prevents a single giant tool output from blowing up the summarizer call itself.

### Multi-Stage Summarization
Large transcripts are split by token share, each chunk summarized separately, then merged into a final summary.

### Tool-Result Details Stripping
`toolResult.details` (internal metadata fields) are removed before building the summarizer payload. Only the content visible to the LLM is summarized.

### Checkpoint and Rollback
Before compaction runs, the session tree is snapshotted. If compaction fails (timeout, summary error, aborted), the session is restored from the snapshot — no half-compacted state ever persists.

### Token Sanity Check
If the post-compaction token estimate is somehow larger than the pre-compaction estimate, `tokens_after` is reported as `None` rather than the bogus value.

## Reason Codes

Every `CompactResult` carries a `reason_code` for observability:

| Code | Meaning |
|---|---|
| `compacted` | Success |
| `no_compactable_entries` | Nothing compactable (e.g., only heartbeats) |
| `below_threshold` | Under the trigger threshold |
| `already_compacted_recently` | Skipped (recent compaction exists) |
| `live_context_still_exceeds_target` | Compacted but still over budget |
| `guard_blocked` | Policy/safeguard prevented |
| `summary_failed` | LLM summarization errored |
| `timeout` | Exceeded safety timeout |
| `aborted` | External abort fired |
| `provider_error_4xx` / `provider_error_5xx` | Provider HTTP error |
| `unknown` | Unclassified |

## Hooks

Plugins can register `before_compaction(ctx)` / `after_compaction(ctx, result)` on `AgentHook`. Hook exceptions are caught and logged — they never abort compaction. Memory plugins use this to sync long-term memory at compaction boundaries.

## See Also

- `timeouts-and-abort.md` — timeout and cancellation configuration
- `architecture-decisions.md` D17/D18 — session DAG and storage protocols
- `upstream-compaction-audit.md` — inventory of upstream behaviors adopted
