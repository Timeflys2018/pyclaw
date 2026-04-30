from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMError, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolContext, ToolRegistry, text_result
from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.core.hooks import (
    AgentHook,
    CompactionContext,
    HookRegistry,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)
from pyclaw.models import (
    AgentRunConfig,
    CompactionConfig,
    CompactResult,
    Done,
    ErrorEvent,
    MessageEntry,
    RetryConfig,
    SessionHeader,
    SessionTree,
    TimeoutConfig,
    ToolResult,
    generate_entry_id,
)
from pyclaw.storage.session.base import InMemorySessionStore


class _SlowStreamingLLM(LLMClient):
    async def stream(  # type: ignore[override]
        self,
        *,
        messages,
        model=None,
        tools=None,
        system=None,
        idle_seconds: float = 0.0,
        abort_event=None,
    ):
        yield LLMStreamChunk(text_delta="starting up")
        await asyncio.sleep(10)
        yield LLMStreamChunk(text_delta="never arrives")


class _InfiniteUnknownToolLLM(LLMClient):
    def __init__(self) -> None:
        super().__init__(default_model="fake")
        self.count = 0

    async def stream(  # type: ignore[override]
        self,
        *,
        messages,
        model=None,
        tools=None,
        system=None,
        idle_seconds: float = 0.0,
        abort_event=None,
    ):
        self.count += 1
        yield LLMStreamChunk(
            tool_call_deltas=[
                {
                    "index": 0,
                    "id": f"c{self.count}",
                    "type": "function",
                    "function": {
                        "name": "fake_tool",
                        "arguments": f'{{"_call_id":"c{self.count}"}}',
                    },
                }
            ]
        )
        yield LLMStreamChunk(finish_reason="tool_calls", usage=LLMUsage())


class _OverflowThenRecoverLLM(LLMClient):
    def __init__(self) -> None:
        super().__init__(default_model="fake")
        self.call_count = 0

    async def stream(  # type: ignore[override]
        self,
        *,
        messages,
        model=None,
        tools=None,
        system=None,
        idle_seconds: float = 0.0,
        abort_event=None,
    ):
        self.call_count += 1
        if self.call_count == 1:
            raise LLMError("context_overflow", "context too large")
        yield LLMStreamChunk(text_delta="post-compaction answer")
        yield LLMStreamChunk(finish_reason="stop", usage=LLMUsage())


class _RecordingHook:
    def __init__(self) -> None:
        self.before_compactions: list[CompactionContext] = []
        self.after_compactions: list[tuple[CompactionContext, CompactResult]] = []

    async def before_prompt_build(
        self, context: PromptBuildContext
    ) -> PromptBuildResult | None:
        return None

    async def after_response(self, observation: ResponseObservation) -> None:
        return None

    async def before_compaction(self, ctx: CompactionContext) -> None:
        self.before_compactions.append(ctx)

    async def after_compaction(self, ctx: CompactionContext, result: CompactResult) -> None:
        self.after_compactions.append((ctx, result))


@pytest.mark.asyncio
async def test_e2e_abort_mid_stream_yields_clean_termination(tmp_path: Path) -> None:
    llm = _SlowStreamingLLM(default_model="fake")
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            timeouts=TimeoutConfig(run_seconds=10.0, idle_seconds=0.0),
        ),
    )
    abort = asyncio.Event()

    async def _cancel() -> None:
        await asyncio.sleep(0.05)
        abort.set()

    asyncio.create_task(_cancel())
    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="e2e1", workspace_id="default", agent_id="main", user_message="hi"),
        deps,
        tool_workspace_path=tmp_path,
        abort=abort,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors
    assert errors[-1].error_code == "aborted"


@pytest.mark.asyncio
async def test_e2e_tool_loop_pathological_case(tmp_path: Path) -> None:
    llm = _InfiniteUnknownToolLLM()
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            max_iterations=50,
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=0,
                empty_response_limit=0,
                unknown_tool_threshold=3,
            ),
        ),
    )
    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="e2e2", workspace_id="default", agent_id="main", user_message="go"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors and errors[-1].error_code == "tool_loop"
    assert llm.count < 50


@pytest.mark.asyncio
async def test_e2e_compaction_triggered_on_context_overflow_with_hooks(tmp_path: Path) -> None:
    llm = _OverflowThenRecoverLLM()
    hook = _RecordingHook()
    hooks = HookRegistry()
    hooks.register(hook)

    async def _summarizer(payload, *, model=None):
        return "session summary"

    store = InMemorySessionStore()
    header = SessionHeader(id="e2e3", workspace_id="default", agent_id="main")
    tree = SessionTree(header=header)
    prior_parent: str | None = None
    for i in range(8):
        role = "user" if i % 2 == 0 else "assistant"
        entry = MessageEntry(
            id=generate_entry_id(set(tree.entries.keys())),
            parent_id=prior_parent,
            role=role,
            content=("prior " * 80) + f"msg {i}",
        )
        tree.append(entry)
        prior_parent = entry.id
    await store.save_header(tree)

    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        hooks=hooks,
        session_store=store,
        context_engine=DefaultContextEngine(
            summarize=_summarizer,
            keep_recent_tokens=50,
            compaction_timeout_s=5.0,
        ),
        config=AgentRunConfig(
            context_window=500,
            compaction=CompactionConfig(model="openai/gpt-4o-mini"),
        ),
    )

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(
            session_id="e2e3",
            workspace_id="default",
            agent_id="main",
            user_message="now answer my new question",
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors, f"unexpected errors: {errors}"
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "post-compaction answer"

    assert len(hook.before_compactions) == 1
    assert len(hook.after_compactions) == 1
    assert hook.after_compactions[0][1].ok is True
