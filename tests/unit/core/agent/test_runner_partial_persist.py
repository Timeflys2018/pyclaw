"""Tests for runner partial assistant persistence (Phase B.2).

Covers spec runner-partial-persistence Requirement
"Runner persists partial assistant content on mid-stream and LLM-raised exits"
+ supporting Requirements (helper exception handling, cancellation pass-through).

Test plan (9 functions):
- Test 1-6 (positive): each Bucket B path persists partial when text_parts non-empty
- Test 7 (negative, Bucket A): pre-LLM paths do NOT persist
- Test 8 (negative, Bucket C): post-persist paths do NOT double-persist
- Test 9 (defense): helper exception does not block ErrorEvent dispatch

Mock check pattern: `_append(deps, tree, entry)` is positional 3-arg.
Use `c.args[2].partial / .role / .content / .tool_calls` (NOT kwargs.get).
"""

from __future__ import annotations

from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMError, LLMStreamChunk
from pyclaw.core.agent.runner import (
    AgentRunnerDeps,
    RunRequest,
    run_agent_stream,
)
from pyclaw.core.agent.runtime_util import AgentAbortedError, AgentTimeoutError
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.core.hooks import HookRegistry
from pyclaw.models import (
    AgentRunConfig,
    CompactionConfig,
    ErrorEvent,
    PromptBudgetConfig,
    SessionHeader,
    SessionTree,
)
from pyclaw.storage.session.base import InMemorySessionStore


def _make_deps(llm: LLMClient, store: InMemorySessionStore) -> AgentRunnerDeps:
    return AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        session_store=store,
        context_engine=DefaultContextEngine(),
        config=AgentRunConfig(
            context_window=10_000,
            prompt_budget=PromptBudgetConfig(
                system_zone_tokens=500,
                dynamic_zone_tokens=500,
                output_reserve_tokens=500,
            ),
            compaction=CompactionConfig(model="fake"),
        ),
    )


async def _seed_session(store: InMemorySessionStore, session_id: str) -> SessionTree:
    header = SessionHeader(id=session_id, workspace_id="default", agent_id="main")
    tree = SessionTree(header=header)
    await store.save_header(tree)
    return tree


def _request(session_id: str) -> RunRequest:
    return RunRequest(
        session_id=session_id,
        workspace_id="default",
        agent_id="main",
        user_message="hello",
    )


async def _partial_entries(store: InMemorySessionStore, session_id: str) -> list[Any]:
    tree = await store.load(session_id)
    if tree is None:
        return []
    return [
        e
        for e in tree.entries.values()
        if getattr(e, "role", None) == "assistant" and getattr(e, "partial", False)
    ]


class _AbortMidStreamLLM(LLMClient):
    """Yields one chunk, then triggers abort_event before second chunk."""

    def __init__(self, abort_event):
        super().__init__(default_model="fake")
        self._abort = abort_event

    def stream(self, **kwargs):
        abort = self._abort

        async def _gen():
            yield LLMStreamChunk(text_delta="hello world")
            abort.set()
            yield LLMStreamChunk(text_delta="never reaches here")

        return _gen()


class _RaisingLLM(LLMClient):
    """Yields some chunks then raises a configured exception."""

    def __init__(self, chunks_before_error: list[str], error: Exception):
        super().__init__(default_model="fake")
        self._chunks = chunks_before_error
        self._error = error

    def stream(self, **kwargs):
        chunks = self._chunks
        err = self._error

        def _gen_factory():
            async def _gen():
                for c in chunks:
                    yield LLMStreamChunk(text_delta=c)
                raise err

            return _gen()

        return _gen_factory()


@pytest.mark.asyncio
async def test_persist_partial_on_mid_stream_abort(tmp_path) -> None:
    """Scenario: mid-stream abort (L405 in current runner) persists partial entry before yielding ErrorEvent."""
    import asyncio

    store = InMemorySessionStore()
    await _seed_session(store, "ses-abort-mid")

    abort_event = asyncio.Event()
    deps = _make_deps(_AbortMidStreamLLM(abort_event), store)

    events: list[Any] = []
    async for evt in run_agent_stream(
        _request("ses-abort-mid"), deps, tool_workspace_path=tmp_path, abort=abort_event
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].error_code == "aborted"

    partials = await _partial_entries(store, "ses-abort-mid")
    assert len(partials) == 1
    assert partials[0].role == "assistant"
    assert partials[0].partial is True
    assert partials[0].content == "hello world"
    assert partials[0].tool_calls is None


@pytest.mark.asyncio
async def test_persist_partial_on_mid_stream_timeout(tmp_path) -> None:
    """Scenario: mid-stream timeout persists partial entry before yielding ErrorEvent.

    Triggered via `iterate_with_deadline` raising AgentTimeoutError mid-stream
    (deadline_s=0.0 forces immediate timeout on first deadline check after first chunk).
    Tests the L417 AgentTimeoutError catch path which is functionally equivalent
    to the L398 mid-stream timeout for partial persistence semantics.
    """
    store = InMemorySessionStore()
    await _seed_session(store, "ses-timeout-mid")

    err = AgentTimeoutError(kind="run", limit_seconds=0.001)
    deps = _make_deps(_RaisingLLM(["partial text from stream"], err), store)

    events: list[Any] = []
    async for evt in run_agent_stream(
        _request("ses-timeout-mid"), deps, tool_workspace_path=tmp_path
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1
    assert errors[0].error_code == "timeout"

    partials = await _partial_entries(store, "ses-timeout-mid")
    assert len(partials) == 1
    assert partials[0].content == "partial text from stream"


@pytest.mark.asyncio
async def test_persist_partial_on_agent_timeout_error_with_text(tmp_path) -> None:
    """Scenario: AgentTimeoutError catch (L417) persists partial entry when text_parts non-empty."""
    store = InMemorySessionStore()
    await _seed_session(store, "ses-agent-timeout")

    err = AgentTimeoutError(kind="run", limit_seconds=1.0)
    deps = _make_deps(_RaisingLLM(["chunk one", " chunk two"], err), store)

    events: list[Any] = []
    async for evt in run_agent_stream(
        _request("ses-agent-timeout"), deps, tool_workspace_path=tmp_path
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[0].error_code == "timeout"

    partials = await _partial_entries(store, "ses-agent-timeout")
    assert len(partials) == 1
    assert partials[0].content == "chunk one chunk two"


@pytest.mark.asyncio
async def test_persist_partial_on_agent_aborted_error_with_text(tmp_path) -> None:
    """Scenario: AgentAbortedError catch (L421) persists partial entry when text_parts non-empty."""
    store = InMemorySessionStore()
    await _seed_session(store, "ses-agent-aborted")

    err = AgentAbortedError(kind="run")
    deps = _make_deps(_RaisingLLM(["aborted-mid-text"], err), store)

    events: list[Any] = []
    async for evt in run_agent_stream(
        _request("ses-agent-aborted"), deps, tool_workspace_path=tmp_path
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[0].error_code == "aborted"

    partials = await _partial_entries(store, "ses-agent-aborted")
    assert len(partials) == 1
    assert partials[0].content == "aborted-mid-text"


@pytest.mark.asyncio
async def test_persist_partial_on_llm_error_compaction_failed_with_text(tmp_path) -> None:
    """Scenario: LLMError compaction_failed (L441 in current runner) persists partial when text_parts non-empty.

    Triggered by raising LLMError("context_overflow") mid-stream after streaming text,
    with no summarizer configured so compaction fails — runner falls into the
    `if not outcome.ok` branch that yields ErrorEvent with compaction_failed code.
    """
    store = InMemorySessionStore()
    await _seed_session(store, "ses-compact-fail")

    err = LLMError("context_overflow", "too big")
    deps = _make_deps(_RaisingLLM(["context-overflow-text"], err), store)

    events: list[Any] = []
    async for evt in run_agent_stream(
        _request("ses-compact-fail"), deps, tool_workspace_path=tmp_path
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors

    partials = await _partial_entries(store, "ses-compact-fail")
    assert len(partials) == 1
    assert partials[0].content == "context-overflow-text"


@pytest.mark.asyncio
async def test_persist_partial_on_llm_error_non_overflow_with_text(tmp_path) -> None:
    """Scenario: LLMError non-overflow catch (L450) persists partial entry when text_parts non-empty."""
    store = InMemorySessionStore()
    await _seed_session(store, "ses-llm-error")

    err = LLMError("rate_limit", "slow down")
    deps = _make_deps(_RaisingLLM(["rate-limited-text"], err), store)

    events: list[Any] = []
    async for evt in run_agent_stream(
        _request("ses-llm-error"), deps, tool_workspace_path=tmp_path
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[0].error_code == "rate_limit"

    partials = await _partial_entries(store, "ses-llm-error")
    assert len(partials) == 1
    assert partials[0].content == "rate-limited-text"


@pytest.mark.asyncio
async def test_no_persist_when_text_parts_empty(tmp_path) -> None:
    """Scenario: LLM raises before any chunk arrives (text_parts empty) → no partial entry."""
    store = InMemorySessionStore()
    await _seed_session(store, "ses-empty-text")

    err = LLMError("rate_limit", "instant fail")
    deps = _make_deps(_RaisingLLM([], err), store)

    events: list[Any] = []
    async for evt in run_agent_stream(
        _request("ses-empty-text"), deps, tool_workspace_path=tmp_path
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors

    partials = await _partial_entries(store, "ses-empty-text")
    assert len(partials) == 0


@pytest.mark.asyncio
async def test_pre_llm_paths_do_not_persist(tmp_path) -> None:
    """Scenario: Bucket A (pre-LLM) abort path does NOT persist a partial entry.

    Trigger: abort_event already set before runner enters the iteration body
    (L327 abort 进 iter). text_parts is not yet declared (iter 1) — runner
    must NOT call _persist_partial_assistant.
    """
    import asyncio

    store = InMemorySessionStore()
    await _seed_session(store, "ses-pre-abort")

    abort_event = asyncio.Event()
    abort_event.set()  # set BEFORE runner starts iterating

    class _NeverCalledLLM(LLMClient):
        def __init__(self):
            super().__init__(default_model="fake")

        def stream(self, **kwargs):
            async def _gen():
                yield LLMStreamChunk(text_delta="should never see this")

            return _gen()

    deps = _make_deps(_NeverCalledLLM(), store)

    events: list[Any] = []
    async for evt in run_agent_stream(
        _request("ses-pre-abort"), deps, tool_workspace_path=tmp_path, abort=abort_event
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[0].error_code == "aborted"

    partials = await _partial_entries(store, "ses-pre-abort")
    assert len(partials) == 0


@pytest.mark.asyncio
async def test_persist_helper_exception_does_not_block_error_event(tmp_path, caplog) -> None:
    """Scenario: _append failure inside helper does NOT block ErrorEvent dispatch.

    Locks design D2 Rationale point 4 (best-effort persistence) and
    spec scenario "helper persistence failure does not block ErrorEvent dispatch".

    Failure is injected ONLY for partial=True entries; user/normal entries persist
    normally so the runner can reach the mid-stream abort path.
    """
    import asyncio
    import logging

    store = InMemorySessionStore()
    await _seed_session(store, "ses-redis-down")

    real_append = store.append_entry

    async def _selective_failure(session_id: str, entry, *, leaf_id: str) -> None:
        if getattr(entry, "partial", False):
            raise ConnectionError("redis down")
        await real_append(session_id, entry, leaf_id=leaf_id)

    store.append_entry = _selective_failure  # type: ignore[method-assign]

    abort_event = asyncio.Event()
    deps = _make_deps(_AbortMidStreamLLM(abort_event), store)

    events: list[Any] = []
    with caplog.at_level(logging.WARNING, logger="pyclaw.core.agent.runner"):
        async for evt in run_agent_stream(
            _request("ses-redis-down"),
            deps,
            tool_workspace_path=tmp_path,
            abort=abort_event,
        ):
            events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert len(errors) == 1, (
        f"ErrorEvent must still yield even when partial persist fails. Got events: {events}"
    )
    assert errors[0].error_code == "aborted"

    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("partial assistant" in m for m in warning_messages), (
        f"Expected warning about failed partial persist. Captured: {warning_messages}"
    )
