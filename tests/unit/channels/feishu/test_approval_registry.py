from __future__ import annotations

import asyncio

import pytest

from pyclaw.channels.feishu.approval_registry import (
    FeishuApprovalRegistry,
    FeishuPendingDecision,
)


class TestPendingLifecycle:
    @pytest.mark.asyncio
    async def test_create_returns_unset_event(self) -> None:
        reg = FeishuApprovalRegistry()
        p = reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )
        assert isinstance(p, FeishuPendingDecision)
        assert not p.event.is_set()
        assert p.approved is None
        assert p.originator_open_id == "ou_a"

    @pytest.mark.asyncio
    async def test_set_decision_signals_event_and_records(self) -> None:
        reg = FeishuApprovalRegistry()
        p = reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )
        assert reg.set_decision(
            conv_id="c1",
            tool_call_id="x1",
            approved=True,
            operator_open_id="ou_a",
        )
        assert p.event.is_set()
        assert p.approved is True
        assert p.operator_open_id == "ou_a"

    @pytest.mark.asyncio
    async def test_set_decision_returns_false_when_not_pending(self) -> None:
        reg = FeishuApprovalRegistry()
        ok = reg.set_decision(
            conv_id="c1",
            tool_call_id="x1",
            approved=True,
            operator_open_id="ou_a",
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_discard_removes_entry(self) -> None:
        reg = FeishuApprovalRegistry()
        reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )
        reg.discard(conv_id="c1", tool_call_id="x1")
        assert reg.get(conv_id="c1", tool_call_id="x1") is None

    @pytest.mark.asyncio
    async def test_reset_signals_outstanding_with_deny(self) -> None:
        reg = FeishuApprovalRegistry()
        p1 = reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )
        p2 = reg.create_pending(
            conv_id="c2",
            tool_call_id="x2",
            originator_open_id="ou_b",
        )
        reg.reset()
        assert p1.event.is_set() and p1.approved is False
        assert p2.event.is_set() and p2.approved is False
        assert reg.get(conv_id="c1", tool_call_id="x1") is None

    @pytest.mark.asyncio
    async def test_wait_decision_resolves_on_set(self) -> None:
        reg = FeishuApprovalRegistry()
        p = reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )

        async def respond_later() -> None:
            await asyncio.sleep(0.01)
            reg.set_decision(
                conv_id="c1",
                tool_call_id="x1",
                approved=True,
                operator_open_id="ou_a",
            )

        gate = asyncio.create_task(respond_later())
        result = await p.wait_decision(timeout_seconds=1.0)
        await gate
        assert result is True

    @pytest.mark.asyncio
    async def test_wait_decision_raises_on_timeout(self) -> None:
        reg = FeishuApprovalRegistry()
        p = reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )
        with pytest.raises(asyncio.TimeoutError):
            await p.wait_decision(timeout_seconds=0.01)
