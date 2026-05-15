from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from pyclaw.channels.feishu.approval_card import APPROVAL_ACTION_TYPE
from pyclaw.channels.feishu.approval_registry import FeishuApprovalRegistry

logger = logging.getLogger(__name__)


def make_card_action_handler(
    registry: FeishuApprovalRegistry,
    main_loop: asyncio.AbstractEventLoop,
) -> Any:
    """Build the synchronous ``register_p2_card_action_trigger`` callback.

    Receives a Feishu ``card.action.trigger`` event from the WS dispatcher
    thread (per-event call, must return within 3 seconds). Routes by the
    embedded ``action.value.type``:
    - If type is :data:`APPROVAL_ACTION_TYPE` and operator matches the
      stored originator, records the decision in ``registry`` and returns
      a toast + replacement card.
    - If non-originator clicks, returns a toast (no card change).
    - If unknown type, returns empty response.
    """

    def handler(data: Any) -> Any:
        try:
            return _route(data, registry, main_loop)
        except Exception:
            logger.exception("card.action.trigger handler crashed")
            return _empty_response()

    return handler


def _route(
    data: Any,
    registry: FeishuApprovalRegistry,
    main_loop: asyncio.AbstractEventLoop,
) -> Any:
    event = getattr(data, "event", None)
    if event is None:
        return _empty_response()

    action = getattr(event, "action", None)
    if action is None:
        return _empty_response()

    raw_value = getattr(action, "value", None)
    if isinstance(raw_value, str):
        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            return _empty_response()
    elif isinstance(raw_value, dict):
        value = raw_value
    else:
        return _empty_response()

    if value.get("type") != APPROVAL_ACTION_TYPE:
        return _empty_response()

    operator = getattr(event, "operator", None)
    operator_open_id = getattr(operator, "open_id", "") or ""
    originator = value.get("originator_open_id", "")
    conv_id = value.get("conv_id", "")
    tool_call_id = value.get("tool_call_id", "")
    decision = value.get("decision", "deny")
    tool_name = value.get("tool_name", "tool")

    if operator_open_id and originator and operator_open_id != originator:
        return _toast_only(
            "Only the originator can approve/deny this action.",
            i18n={
                "zh_cn": "仅发起人可以审批此操作",
                "en_us": "Only the originator can approve/deny this action.",
            },
        )

    approved = decision == "approve"
    asyncio.run_coroutine_threadsafe(
        _record_decision(
            registry,
            conv_id=conv_id,
            tool_call_id=tool_call_id,
            approved=approved,
            operator_open_id=operator_open_id,
        ),
        main_loop,
    )

    return _terminal_response(tool_name=tool_name, decision=decision)


async def _record_decision(
    registry: FeishuApprovalRegistry,
    *,
    conv_id: str,
    tool_call_id: str,
    approved: bool,
    operator_open_id: str,
) -> None:
    registry.set_decision(
        conv_id=conv_id,
        tool_call_id=tool_call_id,
        approved=approved,
        operator_open_id=operator_open_id,
    )


def _empty_response() -> Any:
    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )
    except ImportError:
        return {}
    return P2CardActionTriggerResponse({})


def _toast_only(content: str, *, i18n: dict[str, str] | None = None) -> Any:
    payload: dict[str, Any] = {"toast": {"type": "warning", "content": content}}
    if i18n:
        payload["toast"]["i18n"] = i18n
    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )
    except ImportError:
        return payload
    return P2CardActionTriggerResponse(payload)


def _terminal_response(*, tool_name: str, decision: str) -> Any:
    from pyclaw.channels.feishu.approval_card import build_resolved_card

    card_json = build_resolved_card(tool_name=tool_name, decision=decision)
    payload: dict[str, Any] = {
        "toast": {
            "type": "success" if decision == "approve" else "info",
            "content": (
                f"Approved: {tool_name}" if decision == "approve" else f"Denied: {tool_name}"
            ),
        },
        "card": {
            "type": "raw",
            "data": json.loads(card_json),
        },
    }
    try:
        from lark_oapi.event.callback.model.p2_card_action_trigger import (
            P2CardActionTriggerResponse,
        )
    except ImportError:
        return payload
    return P2CardActionTriggerResponse(payload)
