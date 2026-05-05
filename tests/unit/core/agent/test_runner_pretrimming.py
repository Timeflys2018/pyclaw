from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMError, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import (
    AgentRunnerDeps,
    RunRequest,
    _try_compaction,
    run_agent_stream,
)
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.core.hooks import HookRegistry
from pyclaw.models import (
    AgentRunConfig,
    CompactionConfig,
    CompactResult,
    Done,
    ErrorEvent,
    MessageEntry,
    PromptBudgetConfig,
    SessionHeader,
    SessionTree,
    generate_entry_id,
)
from pyclaw.storage.session.base import InMemorySessionStore


def _make_long_tree(
    store: InMemorySessionStore, session_id: str, msg_count: int, chars_per_msg: int
) -> SessionTree:
    header = SessionHeader(id=session_id, workspace_id="default", agent_id="main")
    tree = SessionTree(header=header)
    prior: str | None = None
    for i in range(msg_count):
        role = "user" if i % 2 == 0 else "assistant"
        entry = MessageEntry(
            id=generate_entry_id(set(tree.entries.keys())),
            parent_id=prior,
            role=role,
            content=("x" * chars_per_msg) + f" msg {i}",
        )
        tree.append(entry)
        prior = entry.id
    return tree


class _FakeLLM(LLMClient):
    def __init__(self, model: str = "fake", final_text: str = "ok") -> None:
        super().__init__(default_model=model)
        self.stream_calls = 0
        self._final_text = final_text

    def stream(self, **kwargs):
        llm = self

        async def _gen():
            llm.stream_calls += 1
            yield LLMStreamChunk(text_delta=llm._final_text)
            yield LLMStreamChunk(finish_reason="stop", usage=LLMUsage())

        return _gen()


def _budget_with_history(history_tokens: int) -> PromptBudgetConfig:
    return PromptBudgetConfig(
        system_zone_tokens=100,
        dynamic_zone_tokens=100,
        output_reserve_tokens=100,
    )


async def test_pretrim_triggers_compaction_when_history_exceeds_budget(tmp_path) -> None:
    store = InMemorySessionStore()
    tree = _make_long_tree(store, "pre-trim", msg_count=50, chars_per_msg=4000)
    await store.save_header(tree)

    summarizer_called = []

    async def _summarizer(payload, *, model=None):
        summarizer_called.append(1)
        return "compacted summary"

    engine = DefaultContextEngine(
        summarize=_summarizer,
        keep_recent_tokens=50,
        compaction_timeout_s=5.0,
    )

    deps = AgentRunnerDeps(
        llm=_FakeLLM(final_text="response after pretrim"),
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        session_store=store,
        context_engine=engine,
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

    events: list[Any] = []
    async for evt in run_agent_stream(
        RunRequest(
            session_id="pre-trim",
            workspace_id="default",
            agent_id="main",
            user_message="hello",
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors
    done = [e for e in events if isinstance(e, Done)]
    assert done and done[0].final_message == "response after pretrim"
    assert len(summarizer_called) >= 1


async def test_pretrim_skipped_when_budget_zero(tmp_path) -> None:
    store = InMemorySessionStore()
    tree = _make_long_tree(store, "budget-zero", msg_count=2, chars_per_msg=100)
    await store.save_header(tree)

    summarizer_called = []

    async def _summarizer(payload, *, model=None):
        summarizer_called.append(1)
        return "should not be called"

    engine = DefaultContextEngine(
        summarize=_summarizer,
        keep_recent_tokens=10,
        compaction_timeout_s=5.0,
    )

    deps = AgentRunnerDeps(
        llm=_FakeLLM(final_text="ok"),
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        session_store=store,
        context_engine=engine,
        config=AgentRunConfig(
            context_window=500,
            compaction=CompactionConfig(model="fake"),
        ),
    )

    events: list[Any] = []
    async for evt in run_agent_stream(
        RunRequest(
            session_id="budget-zero",
            workspace_id="default",
            agent_id="main",
            user_message="hello",
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(evt)

    assert summarizer_called == []


async def test_try_compaction_helper_returns_ok_when_compacted() -> None:
    store = InMemorySessionStore()
    tree = _make_long_tree(store, "helper-test", msg_count=6, chars_per_msg=500)
    await store.save_header(tree)

    async def _summarizer(payload, *, model=None):
        return "compact summary"

    engine = DefaultContextEngine(
        summarize=_summarizer,
        keep_recent_tokens=50,
        compaction_timeout_s=5.0,
    )

    deps = AgentRunnerDeps(
        llm=_FakeLLM(),
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        session_store=store,
        context_engine=engine,
        config=AgentRunConfig(
            context_window=2000,
            prompt_budget=PromptBudgetConfig(
                system_zone_tokens=200,
                dynamic_zone_tokens=200,
                output_reserve_tokens=200,
            ),
            compaction=CompactionConfig(model="fake"),
        ),
    )

    import asyncio as _asyncio

    request = RunRequest(
        session_id="helper-test",
        workspace_id="default",
        agent_id="main",
        user_message="hi",
    )
    base_messages = tree.build_session_context()

    outcome = await _try_compaction(
        deps,
        tree,
        request,
        base_messages,
        history_budget=200,
        abort_event=_asyncio.Event(),
        force=True,
    )

    assert outcome.ok is True
    assert outcome.compacted is True


async def test_try_compaction_helper_ok_false_on_summarizer_exception() -> None:
    import asyncio as _asyncio

    store = InMemorySessionStore()
    tree = _make_long_tree(store, "helper-err", msg_count=6, chars_per_msg=500)
    await store.save_header(tree)

    async def _summarizer(payload, *, model=None):
        raise RuntimeError("summarizer boom")

    engine = DefaultContextEngine(
        summarize=_summarizer,
        keep_recent_tokens=50,
        compaction_timeout_s=5.0,
    )

    deps = AgentRunnerDeps(
        llm=_FakeLLM(),
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        session_store=store,
        context_engine=engine,
        config=AgentRunConfig(
            context_window=2000,
            prompt_budget=PromptBudgetConfig(
                system_zone_tokens=200,
                dynamic_zone_tokens=200,
                output_reserve_tokens=200,
            ),
            compaction=CompactionConfig(model="fake"),
        ),
    )

    request = RunRequest(
        session_id="helper-err",
        workspace_id="default",
        agent_id="main",
        user_message="hi",
    )
    base_messages = tree.build_session_context()

    outcome = await _try_compaction(
        deps,
        tree,
        request,
        base_messages,
        history_budget=200,
        abort_event=_asyncio.Event(),
        force=True,
    )

    assert outcome.ok is False
    assert outcome.error_code in ("summary_failed", "compaction_failed")


async def test_context_overflow_path_still_uses_try_compaction(tmp_path) -> None:
    store = InMemorySessionStore()
    tree = _make_long_tree(store, "overflow-test", msg_count=8, chars_per_msg=500)
    await store.save_header(tree)

    summarizer_calls = []

    async def _summarizer(payload, *, model=None):
        summarizer_calls.append(1)
        return "summarized"

    class _OverflowThenOkLLM(LLMClient):
        def __init__(self):
            super().__init__(default_model="fake")
            self.calls = 0

        def stream(self, **kwargs):
            outer = self

            async def _gen():
                outer.calls += 1
                if outer.calls == 1:
                    raise LLMError("context_overflow", "too big")
                yield LLMStreamChunk(text_delta="recovered")
                yield LLMStreamChunk(finish_reason="stop", usage=LLMUsage())

            return _gen()

    engine = DefaultContextEngine(
        summarize=_summarizer,
        keep_recent_tokens=50,
        compaction_timeout_s=5.0,
    )

    deps = AgentRunnerDeps(
        llm=_OverflowThenOkLLM(),
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        session_store=store,
        context_engine=engine,
        config=AgentRunConfig(
            context_window=500,
            compaction=CompactionConfig(model="fake"),
        ),
    )

    events: list[Any] = []
    async for evt in run_agent_stream(
        RunRequest(
            session_id="overflow-test",
            workspace_id="default",
            agent_id="main",
            user_message="question",
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors, f"unexpected errors: {errors}"
    done = [e for e in events if isinstance(e, Done)]
    assert done
    assert done[0].final_message == "recovered"
    assert len(summarizer_calls) >= 1
