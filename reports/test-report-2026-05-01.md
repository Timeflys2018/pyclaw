# PyClaw Test Report — 2026-05-01

## Overview

| Category | Count | Result |
|---|---|---|
| Unit tests | 233 | ✅ All passed |
| Integration tests (Redis) | 8 | ✅ All passed |
| E2E tests (Real LLM) | 6 | ✅ All passed |
| **Total** | **247** | **✅ 0 failures** |

**Git commit:** `a03d955`
**Python:** 3.12.9
**Test runner:** pytest 9.0.3

---

## E2E Test Results (Real LLM)

**Model:** `anthropic/ppio/pa/claude-sonnet-4-6` via Mify-Anthropic
**Endpoint:** `http://model.mify.ai.srv/anthropic`
**Total duration:** 33.71s

| # | Scenario | Test Name | Result | Duration | What was verified |
|---|---|---|---|---|---|
| S1 | 简单问答 | `test_s1_simple_qa` | ✅ PASS | 10.41s | `Done` event yielded; `final_message` non-empty; no `ErrorEvent` |
| S2 | 流式传输顺序 | `test_s2_streaming_order` | ✅ PASS | 1.50s | `TextChunk` arrives before `Done`; input/output token count > 0 |
| S3 | bash 工具调用 | `test_s3_bash_tool_call` | ✅ PASS | 4.16s | `ToolCallStart(bash)` emitted; `PYCLAW_BASH_OK` in tool output; `Done` |
| S4 | 文件读写 | `test_s4_file_read_write` | ✅ PASS | 6.67s | `greeting.txt` created on disk; content contains `Hello from PyClaw` |
| S5 | 多工具多轮 | `test_s5_multi_tool_multi_turn` | ✅ PASS | 7.13s | `write` + `bash` called; `add.py` exists; bash output contains `10` |
| S6 | Session 持久化 | `test_s6_session_persistence` | ✅ PASS | 3.81s | Second turn correctly recalls secret keyword injected in first turn |

**Philosophy:** Observable behavior only — tests assert system behavior (events emitted, files created, session recalled), not LLM wording.

---

## Integration Test Results (Real Redis)

**Redis:** `ares.tj-info-ai-dms-mem0.cache.srv:22300`
**Key prefix:** `pyclaw-test:` (isolated from production)

| Test | Result | What was verified |
|---|---|---|
| `test_save_header_and_load_roundtrip` | ✅ PASS | `SessionTree` survives Redis round trip |
| `test_load_returns_none_for_unknown_session` | ✅ PASS | Unknown session_id → `None` |
| `test_append_entry_order_preserved` | ✅ PASS | 5 entries maintain insertion order |
| `test_leaf_id_tracking` | ✅ PASS | `leaf_id` points to last appended entry |
| `test_ttl_present_after_write` | ✅ PASS | Keys have TTL > 0 after write |
| `test_concurrent_appends_serialized` | ✅ PASS | 4 concurrent appends via lock retry — all 4 entries present |
| `test_data_survives_new_client` | ✅ PASS | Data written by client A readable by fresh client B |
| `test_session_lock_error_on_held_lock` | ✅ PASS | `SessionLockError` raised when lock manually held |

---

## Unit Test Summary

| Module | Tests | Notes |
|---|---|---|
| `core/agent/llm` | 10 | Stream chunk merging, tool call delta assembly, error classification |
| `core/agent/compaction/*` | 42 | Dedup, multi-stage summarize, oversized fallback, checkpoint rollback, all 12 reason codes |
| `core/agent/runtime_util` | 14 | `run_with_timeout`, `iterate_with_idle_timeout`, `iterate_with_deadline`, abort |
| `core/agent/incomplete_turn` | 15 | Planning/reasoning/empty classification, retry messages |
| `core/agent/tool_result_truncation` | 11 | Default cap, per-tool override, UTF-8 safe, marker format |
| `core/agent/tools` (builtin, registry, workspace) | 25 | Bash abort, tool timeout, workspace boundary |
| `core/agent/system_prompt` | 7 | Section assembly, hook injection |
| `core/context_engine` | 12 | Compaction trigger, timeout, summary_failed, heartbeat guard, bogus estimate clamp |
| `core/hooks` | 5 | Compaction hook invocation, exception isolation, optional hooks |
| `models/config` | 9 | TimeoutConfig, RetryConfig, CompactionConfig, reason_code validation |
| `models/session_tree` | 9 | Append, get_branch, build_session_context with compaction |
| `storage/protocols` | 2 | Single SessionStore Protocol across all import paths |
| `storage/redis_lock` | 7 | Acquire, release (correct/wrong token), renew |
| `storage/session_factory` | 5 | Memory/Redis backend selection, unknown backend error |
| `infra/redis` | 12 | Settings aliases, URL building, TTL, ping/close |
| `app/startup` | 3 | /health endpoint, session_store on state, storage type in response |
| **Integration (mock-based)** | 29 | Agent runner loop, streaming order, timeout precision, tool loop, retry counts, session persistence |

**8 skipped:** Redis integration tests (require `PYCLAW_TEST_REDIS_HOST` env var, skipped in CI without credentials)

---

## Bugs Found and Fixed During Testing

The E2E run against the real Anthropic API exposed two bugs that were invisible to all 233 mock-based tests:

### Bug 1 — Tool message content format (Anthropic protocol violation)

| | |
|---|---|
| **Symptom** | `AnthropicException: 'dict' object has no attribute 'strip'` after first tool call |
| **Root cause** | `role=tool` messages sent with `content: "string"` but Anthropic API requires `content: [{"type":"text","text":"..."}]` |
| **Fix** | `_message_entry_to_dict()` in `session.py` wraps string content in list-of-blocks format for `role=tool` entries |
| **Commit** | `a03d955` |

### Bug 2 — Tool call arguments format (LiteLLM protocol mismatch)

| | |
|---|---|
| **Symptom** | Same `AnthropicException` on second LLM turn after tool execution |
| **Root cause** | `finalize_tool_calls()` parsed arguments JSON string → dict; LiteLLM/Anthropic requires arguments to remain as JSON string in the `tool_calls` array |
| **Fix** | `finalize_tool_calls()` keeps `arguments` as JSON string; runner parses to dict only for `ToolCallStart` user-facing event |
| **Commit** | `a03d955` |

**Impact:** Both bugs would have silently broken any real Anthropic API usage. They were only discoverable with a live API endpoint.

---

## Coverage Gaps (Known)

| Area | Status | Notes |
|---|---|---|
| Tool result head+tail truncation | ❌ Not implemented | Upstream uses head+tail strategy; PyClaw currently head-only at 25K cap. Tracked: `fix-tool-result-truncation` proposal. |
| `already_compacted_recently` reason code | ❌ Never emitted | Code has the type but engine never produces it. Tracked: `pyclaw-architecture` task 4.x |
| `live_context_still_exceeds_target` reason code | ❌ Never emitted | Same pattern. |
| AgentRunnerDeps factory | ❌ Missing | No `create_agent_runner_deps(settings)`. Tracked: `pyclaw-architecture` task 4.8 |
| Multi-provider LLM routing | ❌ Missing | Single api_key/api_base per client. Tracked: task 4.9 |
| File session backend | ❌ Stub | `session_backend="file"` does nothing. Tracked: task 3.3 |
| Session affinity | ❌ Missing | Multi-instance sticky routing not implemented. Tracked: task 3.4 |
| Channels (Web/Feishu) | ❌ Stub | No HTTP/WS/Feishu endpoints. Tracked: tasks 6-8 |
| Skills | ❌ Stub | SKILL.md parser, ClawHub client not implemented. Tracked: tasks 5.x |
| Memory / Dreaming | ❌ Stub | Not started. Tracked: tasks 9-10 |

---

## How to Run Tests

```bash
# Unit + integration (no external deps):
.venv/bin/pytest tests/ --ignore=tests/e2e -v

# Storage integration tests (requires Redis):
PYCLAW_TEST_REDIS_HOST=<host> \
PYCLAW_TEST_REDIS_PORT=<port> \
PYCLAW_TEST_REDIS_PASSWORD=<password> \
.venv/bin/pytest tests/integration/storage/ -v -m integration

# E2E tests (requires LLM API):
PYCLAW_LLM_API_KEY=<key> \
PYCLAW_LLM_API_BASE=<base_url> \
.venv/bin/pytest tests/e2e/ -v -m e2e --junitxml=reports/e2e-results.xml
```
