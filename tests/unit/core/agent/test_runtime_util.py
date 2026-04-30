from __future__ import annotations

import asyncio

import pytest

from pyclaw.core.agent.compaction import compact_with_safety_timeout
from pyclaw.core.agent.runtime_util import (
    AgentAbortedError,
    AgentTimeoutError,
    iterate_with_deadline,
    iterate_with_idle_timeout,
    run_with_timeout,
)


@pytest.mark.asyncio
async def test_run_with_timeout_returns_result_when_fast() -> None:
    async def _quick() -> int:
        await asyncio.sleep(0)
        return 42

    result = await run_with_timeout(_quick(), timeout_s=1.0)
    assert result == 42


@pytest.mark.asyncio
async def test_run_with_timeout_raises_on_elapsed() -> None:
    async def _slow() -> None:
        await asyncio.sleep(10)

    with pytest.raises(AgentTimeoutError) as info:
        await run_with_timeout(_slow(), timeout_s=0.05, kind="run")
    assert info.value.kind == "run"


@pytest.mark.asyncio
async def test_run_with_timeout_zero_disables() -> None:
    async def _quick() -> str:
        return "done"

    result = await run_with_timeout(_quick(), timeout_s=0.0)
    assert result == "done"


@pytest.mark.asyncio
async def test_run_with_timeout_abort_event_raises_aborted() -> None:
    abort = asyncio.Event()

    async def _slow() -> None:
        await asyncio.sleep(10)

    async def _signal() -> None:
        await asyncio.sleep(0.01)
        abort.set()

    asyncio.create_task(_signal())
    with pytest.raises(AgentAbortedError):
        await run_with_timeout(_slow(), timeout_s=5.0, abort_event=abort, kind="run")


@pytest.mark.asyncio
async def test_iterate_with_idle_timeout_yields_items_fast() -> None:
    async def _source():
        for i in range(3):
            await asyncio.sleep(0)
            yield i

    collected: list[int] = []
    async for v in iterate_with_idle_timeout(_source(), idle_seconds=0.5):
        collected.append(v)
    assert collected == [0, 1, 2]


@pytest.mark.asyncio
async def test_iterate_with_idle_timeout_triggers_on_stall() -> None:
    async def _source():
        yield 1
        await asyncio.sleep(10)
        yield 2

    with pytest.raises(AgentTimeoutError) as info:
        async for _v in iterate_with_idle_timeout(_source(), idle_seconds=0.05):
            pass
    assert info.value.kind == "idle"


@pytest.mark.asyncio
async def test_iterate_with_idle_timeout_zero_disabled() -> None:
    async def _source():
        yield 1
        yield 2

    collected: list[int] = []
    async for v in iterate_with_idle_timeout(_source(), idle_seconds=0.0):
        collected.append(v)
    assert collected == [1, 2]


@pytest.mark.asyncio
async def test_compact_with_safety_timeout_returns_value() -> None:
    async def _fn() -> str:
        await asyncio.sleep(0)
        return "summary"

    result = await compact_with_safety_timeout(_fn, timeout_s=1.0)
    assert result == "summary"


@pytest.mark.asyncio
async def test_compact_with_safety_timeout_calls_on_cancel() -> None:
    cancelled: list[bool] = []

    async def _slow() -> None:
        await asyncio.sleep(10)

    def _on_cancel() -> None:
        cancelled.append(True)

    with pytest.raises(AgentTimeoutError):
        await compact_with_safety_timeout(_slow, timeout_s=0.01, on_cancel=_on_cancel)
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_compact_with_safety_timeout_aborts() -> None:
    abort = asyncio.Event()
    cancelled: list[bool] = []

    async def _slow() -> None:
        await asyncio.sleep(10)

    def _on_cancel() -> None:
        cancelled.append(True)

    async def _signal() -> None:
        await asyncio.sleep(0.01)
        abort.set()

    asyncio.create_task(_signal())
    with pytest.raises(AgentAbortedError):
        await compact_with_safety_timeout(
            _slow, timeout_s=5.0, abort_event=abort, on_cancel=_on_cancel
        )


@pytest.mark.asyncio
async def test_iterate_with_deadline_yields_items_before_deadline() -> None:
    async def _source():
        for i in range(3):
            await asyncio.sleep(0)
            yield i

    collected: list[int] = []
    async for v in iterate_with_deadline(_source(), deadline_s=1.0):
        collected.append(v)
    assert collected == [0, 1, 2]


@pytest.mark.asyncio
async def test_iterate_with_deadline_raises_on_elapsed() -> None:
    async def _source():
        yield 1
        await asyncio.sleep(5)
        yield 2

    with pytest.raises(AgentTimeoutError) as info:
        async for _v in iterate_with_deadline(_source(), deadline_s=0.05, kind="run"):
            pass
    assert info.value.kind == "run"


@pytest.mark.asyncio
async def test_iterate_with_deadline_zero_disables() -> None:
    async def _source():
        for i in range(3):
            yield i

    collected: list[int] = []
    async for v in iterate_with_deadline(_source(), deadline_s=0.0):
        collected.append(v)
    assert collected == [0, 1, 2]


@pytest.mark.asyncio
async def test_iterate_with_deadline_aborts_on_event() -> None:
    abort = asyncio.Event()

    async def _source():
        yield 1
        await asyncio.sleep(10)
        yield 2

    async def _signal() -> None:
        await asyncio.sleep(0.02)
        abort.set()

    asyncio.create_task(_signal())
    with pytest.raises(AgentAbortedError):
        async for _v in iterate_with_deadline(_source(), deadline_s=5.0, abort_event=abort):
            pass
    assert abort.is_set()
