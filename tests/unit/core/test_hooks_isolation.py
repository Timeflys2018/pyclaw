"""Tests for HookRegistry error isolation.

Per `add-agent-steer-injection` design D11.a: `collect_prompt_additions` and
`notify_response` MUST isolate exceptions thrown by individual hooks so that one
failing hook does not prevent other hooks from contributing, and does not crash
the agent's iteration loop.

This matches the existing try/except pattern in `notify_before_compaction`,
`notify_after_compaction`, `notify_run_start`, and `notify_run_end`.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from pyclaw.core.hooks import (
    HookRegistry,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)

if TYPE_CHECKING:
    from pyclaw.core.agent.run_control import RunControl


def _make_ctx() -> PromptBuildContext:
    return PromptBuildContext(
        session_id="sess_test",
        workspace_id="ws_test",
        agent_id="agent_test",
    )


def _make_observation() -> ResponseObservation:
    return ResponseObservation(session_id="sess_test", assistant_text="hello")


class _RaisingPromptHook:
    """Hook whose before_prompt_build always raises."""

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        raise RuntimeError("intentional failure in before_prompt_build")

    async def after_response(self, observation: ResponseObservation) -> None:
        return None

    async def before_compaction(self, context) -> None:  # noqa: ANN001
        return None

    async def after_compaction(self, context, result) -> None:  # noqa: ANN001
        return None

    async def on_run_start(self, session_id: str, control: RunControl) -> None:
        return None

    async def on_run_end(self, session_id: str, terminated_by: str) -> None:
        return None


class _GoodPromptHook:
    """Hook that contributes a valid prompt addition."""

    def __init__(self, append_text: str = "GOOD") -> None:
        self._append = append_text

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        return PromptBuildResult(append=self._append)

    async def after_response(self, observation: ResponseObservation) -> None:
        return None

    async def before_compaction(self, context) -> None:  # noqa: ANN001
        return None

    async def after_compaction(self, context, result) -> None:  # noqa: ANN001
        return None

    async def on_run_start(self, session_id: str, control: RunControl) -> None:
        return None

    async def on_run_end(self, session_id: str, terminated_by: str) -> None:
        return None


class _RaisingResponseHook:
    """Hook whose after_response always raises."""

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        return None

    async def after_response(self, observation: ResponseObservation) -> None:
        raise RuntimeError("intentional failure in after_response")

    async def before_compaction(self, context) -> None:  # noqa: ANN001
        return None

    async def after_compaction(self, context, result) -> None:  # noqa: ANN001
        return None

    async def on_run_start(self, session_id: str, control: RunControl) -> None:
        return None

    async def on_run_end(self, session_id: str, terminated_by: str) -> None:
        return None


class _CountingResponseHook:
    """Hook that counts after_response invocations."""

    def __init__(self) -> None:
        self.count = 0

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        return None

    async def after_response(self, observation: ResponseObservation) -> None:
        self.count += 1

    async def before_compaction(self, context) -> None:  # noqa: ANN001
        return None

    async def after_compaction(self, context, result) -> None:  # noqa: ANN001
        return None

    async def on_run_start(self, session_id: str, control: RunControl) -> None:
        return None

    async def on_run_end(self, session_id: str, terminated_by: str) -> None:
        return None


@pytest.mark.asyncio
async def test_collect_prompt_additions_isolates_raising_hook(caplog):
    """A hook raising in before_prompt_build does not break other hooks (D11.a)."""
    registry = HookRegistry()
    registry.register(_RaisingPromptHook())
    registry.register(_GoodPromptHook(append_text="ok"))

    with caplog.at_level(logging.ERROR):
        result = await registry.collect_prompt_additions(_make_ctx())

    assert result.append == "ok", (
        "Good hook's contribution must be preserved even when a prior hook raised."
    )
    assert result.prepend is None
    assert any("before_prompt_build hook failed" in record.message for record in caplog.records), (
        "Failing hook's exception should be logged via logger.exception"
    )


@pytest.mark.asyncio
async def test_collect_prompt_additions_isolates_raising_hook_in_different_order(caplog):
    """Order-independence: good hook first, then raising hook, then good hook."""
    registry = HookRegistry()
    registry.register(_GoodPromptHook(append_text="first"))
    registry.register(_RaisingPromptHook())
    registry.register(_GoodPromptHook(append_text="third"))

    with caplog.at_level(logging.ERROR):
        result = await registry.collect_prompt_additions(_make_ctx())

    assert result.append == "first\n\nthird", (
        "Both good hooks' contributions should be joined with blank line separator."
    )


@pytest.mark.asyncio
async def test_collect_prompt_additions_does_not_propagate_exception():
    """The method itself must not raise even when all hooks raise."""
    registry = HookRegistry()
    registry.register(_RaisingPromptHook())
    registry.register(_RaisingPromptHook())

    result = await registry.collect_prompt_additions(_make_ctx())
    assert result.append is None
    assert result.prepend is None


@pytest.mark.asyncio
async def test_collect_prompt_additions_with_no_raising_hooks_unaffected():
    """Regression: behavior unchanged when no hooks raise."""
    registry = HookRegistry()
    registry.register(_GoodPromptHook(append_text="a"))
    registry.register(_GoodPromptHook(append_text="b"))

    result = await registry.collect_prompt_additions(_make_ctx())
    assert result.append == "a\n\nb"


@pytest.mark.asyncio
async def test_notify_response_isolates_raising_hook(caplog):
    """A hook raising in after_response does not prevent other hooks from running (D11.a)."""
    registry = HookRegistry()
    registry.register(_RaisingResponseHook())
    counter = _CountingResponseHook()
    registry.register(counter)

    with caplog.at_level(logging.ERROR):
        await registry.notify_response(_make_observation())

    assert counter.count == 1, "Counting hook must run even when prior hook raised."
    assert any("after_response hook failed" in record.message for record in caplog.records), (
        "Failing hook's exception should be logged"
    )


@pytest.mark.asyncio
async def test_notify_response_does_not_propagate_exception():
    """The method itself must not raise even when all hooks raise."""
    registry = HookRegistry()
    registry.register(_RaisingResponseHook())
    registry.register(_RaisingResponseHook())

    await registry.notify_response(_make_observation())


@pytest.mark.asyncio
async def test_notify_response_with_no_raising_hooks_unaffected():
    """Regression: behavior unchanged when no hooks raise."""
    registry = HookRegistry()
    counter1 = _CountingResponseHook()
    counter2 = _CountingResponseHook()
    registry.register(counter1)
    registry.register(counter2)

    await registry.notify_response(_make_observation())
    assert counter1.count == 1
    assert counter2.count == 1
