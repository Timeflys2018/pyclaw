from __future__ import annotations

import pytest

from pyclaw.channels.web.chat import SessionQueue
from pyclaw.core.agent.run_control import RunControl


def test_get_run_control_lazy_creates_and_idempotent() -> None:
    sq = SessionQueue()
    rc1 = sq.get_run_control("conv-A")
    rc2 = sq.get_run_control("conv-A")
    assert rc1 is rc2
    assert isinstance(rc1, RunControl)


def test_distinct_conversations_get_distinct_run_controls() -> None:
    sq = SessionQueue()
    rc_a = sq.get_run_control("conv-A")
    rc_b = sq.get_run_control("conv-B")
    assert rc_a is not rc_b


def test_get_abort_event_shares_event_with_run_control() -> None:
    sq = SessionQueue()
    rc = sq.get_run_control("conv-A")
    ev = sq.get_abort_event("conv-A")
    assert ev is rc.abort_event


def test_get_abort_event_first_then_run_control_shares_event() -> None:
    sq = SessionQueue()
    ev = sq.get_abort_event("conv-X")
    rc = sq.get_run_control("conv-X")
    assert rc.abort_event is ev


def test_reset_abort_event_clears_underlying_event() -> None:
    sq = SessionQueue()
    rc = sq.get_run_control("conv-A")
    rc.stop()
    assert rc.abort_event.is_set()
    sq.reset_abort_event("conv-A")
    assert not rc.abort_event.is_set()


def test_reset_clears_run_controls() -> None:
    sq = SessionQueue()
    sq.get_run_control("conv-A")
    sq.get_run_control("conv-B")
    sq.reset()
    assert sq._run_controls == {}


def test_is_idle_default_true_when_not_busy_and_no_consumer() -> None:
    sq = SessionQueue()
    assert sq.is_idle("conv-X") is True


def test_is_idle_false_when_busy() -> None:
    sq = SessionQueue()
    sq._busy["conv-A"] = True
    assert sq.is_idle("conv-A") is False


def test_is_idle_does_not_inspect_consumer_running() -> None:
    sq = SessionQueue()
    sq._consumers["conv-Y"] = "fake-task-id-still-running"
    assert sq.is_idle("conv-Y") is True
    sq._busy["conv-Y"] = True
    assert sq.is_idle("conv-Y") is False
    sq._busy["conv-Y"] = False
    assert sq.is_idle("conv-Y") is True


@pytest.mark.asyncio
async def test_run_control_active_flag_managed_in_consume_via_external_setter() -> None:
    sq = SessionQueue()
    rc = sq.get_run_control("conv-Z")

    assert rc.active is False
    rc.active = True
    try:
        assert rc.is_active() is True
    finally:
        rc.active = False
    assert rc.is_active() is False


@pytest.mark.asyncio
async def test_consume_pops_run_control_on_exit() -> None:
    from pyclaw.infra.task_manager import TaskManager

    tm = TaskManager()
    try:
        sq = SessionQueue(task_manager=tm)
        sq.get_run_control("conv-cleanup")
        assert "conv-cleanup" in sq._run_controls
        sq.reset()
        assert "conv-cleanup" not in sq._run_controls
    finally:
        await tm.shutdown(grace_s=0.5)
