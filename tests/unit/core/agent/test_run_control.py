from __future__ import annotations

import asyncio

import pytest

from pyclaw.core.agent.run_control import RunControl


def test_default_state_is_inactive() -> None:
    rc = RunControl()
    assert rc.active is False
    assert rc.abort_event.is_set() is False
    assert rc.is_active() is False


def test_active_flag_alone_makes_is_active_true() -> None:
    rc = RunControl()
    rc.active = True
    assert rc.is_active() is True


def test_stop_sets_abort_event() -> None:
    rc = RunControl()
    rc.stop()
    assert rc.abort_event.is_set() is True


def test_stop_overrides_active_flag() -> None:
    rc = RunControl()
    rc.active = True
    rc.stop()
    assert rc.is_active() is False


def test_stop_is_idempotent() -> None:
    rc = RunControl()
    rc.stop()
    rc.stop()
    rc.stop()
    assert rc.abort_event.is_set() is True
    assert rc.is_active() is False


@pytest.mark.asyncio
async def test_stop_releases_waiters() -> None:
    rc = RunControl()

    async def waiter() -> None:
        await rc.abort_event.wait()

    task = asyncio.create_task(waiter())
    await asyncio.sleep(0)
    assert not task.done()
    rc.stop()
    await asyncio.wait_for(task, timeout=0.5)


def test_no_reserved_fields_present() -> None:
    rc = RunControl()
    assert not hasattr(rc, "steer_buffer")
    assert not hasattr(rc, "side_channel")


def test_chat_done_handled_externally_default_false() -> None:
    rc = RunControl()
    assert rc.chat_done_handled_externally is False


def test_chat_done_handled_externally_is_writable_marker() -> None:
    rc = RunControl()
    rc.chat_done_handled_externally = True
    assert rc.chat_done_handled_externally is True
    rc.chat_done_handled_externally = False
    assert rc.chat_done_handled_externally is False
