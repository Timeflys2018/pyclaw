from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from pyclaw.channels.feishu.approval_card import APPROVAL_ACTION_TYPE
from pyclaw.channels.feishu.approval_registry import FeishuApprovalRegistry
from pyclaw.channels.feishu.card_callback import make_card_action_handler


def _make_event(
    *,
    operator_open_id: str,
    value: dict[str, Any] | str,
) -> SimpleNamespace:
    return SimpleNamespace(
        event=SimpleNamespace(
            operator=SimpleNamespace(open_id=operator_open_id),
            action=SimpleNamespace(value=value),
        ),
    )


def _response_payload(resp: Any) -> dict[str, Any]:
    if isinstance(resp, dict):
        return resp
    payload: dict[str, Any] = {}
    toast = getattr(resp, "toast", None)
    if toast is not None:
        payload["toast"] = toast
    card = getattr(resp, "card", None)
    if card is not None:
        payload["card"] = card
    return payload


class TestRouting:
    @pytest.mark.asyncio
    async def test_unknown_type_returns_empty(self) -> None:
        reg = FeishuApprovalRegistry()
        loop = asyncio.get_event_loop()
        handler = make_card_action_handler(reg, loop)
        ev = _make_event(operator_open_id="ou_a", value={"type": "unknown"})
        resp = handler(ev)
        payload = _response_payload(resp)
        assert "card" not in payload

    @pytest.mark.asyncio
    async def test_originator_approve_records_decision(self) -> None:
        reg = FeishuApprovalRegistry()
        loop = asyncio.get_event_loop()
        pending = reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )
        handler = make_card_action_handler(reg, loop)
        ev = _make_event(
            operator_open_id="ou_a",
            value={
                "type": APPROVAL_ACTION_TYPE,
                "conv_id": "c1",
                "tool_call_id": "x1",
                "originator_open_id": "ou_a",
                "decision": "approve",
                "tool_name": "bash",
            },
        )
        handler(ev)
        await asyncio.sleep(0.05)
        assert pending.event.is_set()
        assert pending.approved is True

    @pytest.mark.asyncio
    async def test_originator_deny_records_decision(self) -> None:
        reg = FeishuApprovalRegistry()
        loop = asyncio.get_event_loop()
        pending = reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )
        handler = make_card_action_handler(reg, loop)
        ev = _make_event(
            operator_open_id="ou_a",
            value={
                "type": APPROVAL_ACTION_TYPE,
                "conv_id": "c1",
                "tool_call_id": "x1",
                "originator_open_id": "ou_a",
                "decision": "deny",
                "tool_name": "bash",
            },
        )
        handler(ev)
        await asyncio.sleep(0.05)
        assert pending.event.is_set()
        assert pending.approved is False


class TestOriginatorEnforcement:
    @pytest.mark.asyncio
    async def test_non_originator_click_rejected(self) -> None:
        reg = FeishuApprovalRegistry()
        loop = asyncio.get_event_loop()
        pending = reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )
        handler = make_card_action_handler(reg, loop)
        ev = _make_event(
            operator_open_id="ou_b",
            value={
                "type": APPROVAL_ACTION_TYPE,
                "conv_id": "c1",
                "tool_call_id": "x1",
                "originator_open_id": "ou_a",
                "decision": "approve",
                "tool_name": "bash",
            },
        )
        resp = handler(ev)
        payload = _response_payload(resp)
        toast = payload.get("toast")
        assert toast is not None
        toast_type = getattr(toast, "type", None) or (
            toast.get("type") if isinstance(toast, dict) else None
        )
        toast_content = getattr(toast, "content", None) or (
            toast.get("content") if isinstance(toast, dict) else ""
        )
        assert toast_type == "warning"
        assert "originator" in (toast_content or "").lower()
        assert payload.get("card") is None
        await asyncio.sleep(0.05)
        assert not pending.event.is_set()

    @pytest.mark.asyncio
    async def test_value_as_json_string_parsed(self) -> None:
        reg = FeishuApprovalRegistry()
        loop = asyncio.get_event_loop()
        pending = reg.create_pending(
            conv_id="c1",
            tool_call_id="x1",
            originator_open_id="ou_a",
        )
        handler = make_card_action_handler(reg, loop)
        value_str = json.dumps(
            {
                "type": APPROVAL_ACTION_TYPE,
                "conv_id": "c1",
                "tool_call_id": "x1",
                "originator_open_id": "ou_a",
                "decision": "approve",
                "tool_name": "bash",
            }
        )
        ev = _make_event(operator_open_id="ou_a", value=value_str)
        handler(ev)
        await asyncio.sleep(0.05)
        assert pending.approved is True
