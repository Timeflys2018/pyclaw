# Timeouts and Abort

PyClaw enforces multi-layer timeouts to keep agent runs bounded and supports cooperative cancellation via an `asyncio.Event` abort signal.

## The Three Timeout Layers

```
run timeout ─── outermost, protects whole turn (default 300s)
  ├── idle timeout ─── LLM has not emitted a token in N seconds (default 60s)
  ├── tool timeout ─── per-tool execution cap (default 120s)
  └── compaction timeout ─── independent, 15-min default
```

## Configuration

```json
{
  "agent": {
    "timeouts": {
      "run_seconds": 300.0,
      "idle_seconds": 60.0,
      "tool_seconds": 120.0,
      "compaction_seconds": 900.0
    }
  }
}
```

Setting any value to `0` **disables** that layer.

| Key | Default | Disabled | Description |
|---|---|---|---|
| `run_seconds` | 300 | `0` | Outermost timeout protecting a whole agent turn. |
| `idle_seconds` | 60 | `0` | Max time without a new LLM chunk before aborting the stream. |
| `tool_seconds` | 120 | `0` | Default per-tool execution cap (tools may override). |
| `compaction_seconds` | 900 | `0` | Safety timeout for a compaction attempt. |

### Why Idle Timeout Matters

A run timeout alone cannot catch LLM streams that hang mid-response. The TCP connection stays open and bytes stop flowing. Idle timeout measures time *between* chunks — if the stream stalls longer than `idle_seconds`, the connection is dropped.

### Per-Tool Override

A tool may declare its own timeout via the `timeout_seconds` class attribute:

```python
class MyLongRunningTool:
    name = "reindex_database"
    timeout_seconds = 3600.0

    async def execute(self, args, context): ...
```

## Abort Signal

`run_agent_stream` accepts an optional `abort: asyncio.Event`:

```python
abort = asyncio.Event()
async for event in run_agent_stream(request, deps, tool_workspace_path=p, abort=abort):
    if user_cancelled:
        abort.set()
```

When `abort.set()` is called, the system propagates cancellation through:

1. **LLM stream** — the current `acompletion` call is cancelled; an `LLMError(code="aborted")` surfaces.
2. **Tool execution** — `ToolContext.abort` is already checked by built-in tools before spawn and large I/O; `BashTool` sends SIGTERM, waits a 2-second grace period, then SIGKILL.
3. **Compaction** — the summarizer call is cancelled; the checkpoint is restored.

The run terminates with `ErrorEvent(error_code="aborted")`.

## Error Codes

Runs that terminate via timeout or abort yield an `ErrorEvent`:

| `error_code` | Meaning |
|---|---|
| `timeout` | Run exceeded `run_seconds` (or idle exceeded while run=0). |
| `aborted` | External `abort.set()` was called. |
| `tool_loop` | Unknown-tool loop detector fired after guidance exhausted. |
| `max_iterations` | Loop reached `max_iterations` cap without terminating. |
| `compaction_failed` | Compaction errored and session was rolled back. |
| `summary_failed` / `provider_error_4xx` / `provider_error_5xx` | Compaction-specific error codes (see compaction-guide.md). |

## Best Practices

- **Production**: Tighten `run_seconds` to your SLO (e.g., 60s for interactive chat). Keep `idle_seconds` at 60s.
- **Batch work**: Raise `run_seconds` to 1800+. Raise tool timeouts for any long commands.
- **Interactive cancellation**: Always pass an `abort` event. Wire it to your client-side "stop" button.
- **Test your abort path**: Ensure downstream cleanup (subprocess kill, file handles) happens reliably.
