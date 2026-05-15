from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.core.commands._helpers import idle_guard_check
from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec


class _FakeQueue:
    def __init__(self, idle: bool) -> None:
        self._idle = idle

    def is_idle(self, key: str) -> bool:
        return self._idle


async def _noop(args: str, ctx: object) -> None:
    return None


def _spec(*, requires_idle: bool) -> CommandSpec:
    return CommandSpec(
        name="/x",
        handler=_noop,
        category="test",
        help_text="x",
        channels=ALL_CHANNELS,
        requires_idle=requires_idle,
    )


@pytest.mark.asyncio
async def test_returns_true_and_replies_when_busy_and_required() -> None:
    reply = AsyncMock()
    rejected = await idle_guard_check(
        _spec(requires_idle=True),
        _FakeQueue(idle=False),
        "key-1",
        reply,
    )
    assert rejected is True
    reply.assert_awaited_once()
    msg = reply.await_args.args[0]
    assert "任务运行中" in msg


@pytest.mark.asyncio
async def test_returns_false_when_idle_and_required() -> None:
    reply = AsyncMock()
    rejected = await idle_guard_check(
        _spec(requires_idle=True),
        _FakeQueue(idle=True),
        "key-1",
        reply,
    )
    assert rejected is False
    reply.assert_not_awaited()


@pytest.mark.asyncio
async def test_returns_false_when_not_required_regardless_of_idle() -> None:
    reply = AsyncMock()

    rejected_busy = await idle_guard_check(
        _spec(requires_idle=False),
        _FakeQueue(idle=False),
        "key",
        reply,
    )
    rejected_idle = await idle_guard_check(
        _spec(requires_idle=False),
        _FakeQueue(idle=True),
        "key",
        reply,
    )

    assert rejected_busy is False
    assert rejected_idle is False
    reply.assert_not_awaited()
