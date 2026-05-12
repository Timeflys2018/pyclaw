"""Tests for check_idle (spec-free idle guard for sub-command gating, Phase A6)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.core.commands._helpers import check_idle, idle_guard_check
from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec


class _FakeQueue:
    def __init__(self, idle: bool) -> None:
        self._idle = idle

    def is_idle(self, key: str) -> bool:
        return self._idle


async def _noop(args: str, ctx: object) -> None:
    return None


@pytest.mark.asyncio
async def test_check_idle_returns_false_when_idle() -> None:
    reply = AsyncMock()
    result = await check_idle(_FakeQueue(idle=True), "test_key", reply)
    assert result is False
    reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_idle_returns_true_and_replies_when_busy() -> None:
    reply = AsyncMock()
    result = await check_idle(_FakeQueue(idle=False), "test_key", reply)
    assert result is True
    reply.assert_awaited_once()
    msg = reply.await_args[0][0]
    assert "/stop" in msg or "任务运行中" in msg


@pytest.mark.asyncio
async def test_check_idle_is_independent_of_command_spec() -> None:
    """Explicit anti-pattern proof: idle_guard_check with requires_idle=False short-circuits.

    This is why check_idle exists as a spec-free alternative for sub-command gating.
    """
    spec = CommandSpec(
        name="/x",
        handler=_noop,
        category="test",
        help_text="x",
        channels=ALL_CHANNELS,
        requires_idle=False,
    )
    reply_guard = AsyncMock()
    result_guard = await idle_guard_check(spec, _FakeQueue(idle=False), "k", reply_guard)
    assert result_guard is False
    reply_guard.assert_not_awaited()

    reply_check = AsyncMock()
    result_check = await check_idle(_FakeQueue(idle=False), "k", reply_check)
    assert result_check is True
    reply_check.assert_awaited_once()
