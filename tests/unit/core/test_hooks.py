from __future__ import annotations

import pytest

from pyclaw.core.hooks import (
    CompactionContext,
    HookRegistry,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)
from pyclaw.models import CompactResult


class _RecordingHook:
    def __init__(self, *, raise_before: bool = False, raise_after: bool = False) -> None:
        self.raise_before = raise_before
        self.raise_after = raise_after
        self.before_calls: list[CompactionContext] = []
        self.after_calls: list[tuple[CompactionContext, CompactResult]] = []

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        return None

    async def after_response(self, observation: ResponseObservation) -> None:
        return None

    async def before_compaction(self, ctx: CompactionContext) -> None:
        self.before_calls.append(ctx)
        if self.raise_before:
            raise RuntimeError("boom-before")

    async def after_compaction(self, ctx: CompactionContext, result: CompactResult) -> None:
        self.after_calls.append((ctx, result))
        if self.raise_after:
            raise RuntimeError("boom-after")


@pytest.mark.asyncio
async def test_before_compaction_hook_invoked() -> None:
    registry = HookRegistry()
    hook = _RecordingHook()
    registry.register(hook)
    ctx = CompactionContext(
        session_id="s1", workspace_id="w", agent_id="a", tokens_before=1000
    )
    await registry.notify_before_compaction(ctx)
    assert hook.before_calls == [ctx]


@pytest.mark.asyncio
async def test_after_compaction_hook_invoked() -> None:
    registry = HookRegistry()
    hook = _RecordingHook()
    registry.register(hook)
    ctx = CompactionContext(session_id="s1", workspace_id="w", agent_id="a")
    result = CompactResult(ok=True, compacted=True, reason_code="compacted")
    await registry.notify_after_compaction(ctx, result)
    assert hook.after_calls == [(ctx, result)]


@pytest.mark.asyncio
async def test_before_compaction_exception_isolated() -> None:
    registry = HookRegistry()
    bad = _RecordingHook(raise_before=True)
    good = _RecordingHook()
    registry.register(bad)
    registry.register(good)
    ctx = CompactionContext(session_id="s1", workspace_id="w", agent_id="a")
    await registry.notify_before_compaction(ctx)
    assert good.before_calls == [ctx]


@pytest.mark.asyncio
async def test_after_compaction_exception_isolated() -> None:
    registry = HookRegistry()
    bad = _RecordingHook(raise_after=True)
    good = _RecordingHook()
    registry.register(bad)
    registry.register(good)
    ctx = CompactionContext(session_id="s1", workspace_id="w", agent_id="a")
    result = CompactResult(ok=True, compacted=True)
    await registry.notify_after_compaction(ctx, result)
    assert good.after_calls == [(ctx, result)]


@pytest.mark.asyncio
async def test_compaction_hooks_optional() -> None:
    class _MinimalHook:
        async def before_prompt_build(
            self, context: PromptBuildContext
        ) -> PromptBuildResult | None:
            return None

        async def after_response(self, observation: ResponseObservation) -> None:
            return None

    registry = HookRegistry()
    registry.register(_MinimalHook())
    ctx = CompactionContext(session_id="s1", workspace_id="w", agent_id="a")
    await registry.notify_before_compaction(ctx)
    await registry.notify_after_compaction(ctx, CompactResult(ok=True, compacted=False))
