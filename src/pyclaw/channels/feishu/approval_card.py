from __future__ import annotations

import json
from typing import Any

APPROVAL_ACTION_TYPE = "pyclaw_tool_approval"


def build_approval_card(
    *,
    conv_id: str,
    tool_call_id: str,
    originator_open_id: str,
    tool_name: str,
    description: str,
    countdown_seconds: int,
) -> str:
    """Build a Card JSON 2.0 approval card with Approve/Deny buttons.

    The routing tuple is embedded in each button's ``behaviors[].value`` dict
    — Feishu echoes it back verbatim in the ``card.action.trigger`` callback.
    The callback handler validates ``operator.open_id == originator_open_id``
    before recording the decision.

    V2 schema requirements (vs the deprecated v1):
    - No ``tag: action`` wrapper. Buttons sit inside a ``column_set`` so they
      render side by side.
    - Each button MUST have a ``name`` field, otherwise clicks silently fail
      to route to ``card.action.trigger`` (Feishu error 200340).
    - The custom data goes inside ``behaviors: [{"type": "callback", "value": ...}]``
      not at the button root.
    """
    return json.dumps(
        _card_skeleton(
            header_template="orange",
            header_title="🛡 Tool Approval Required",
            body_text=_pending_body(tool_name, description, countdown_seconds),
            buttons=_approval_buttons(
                conv_id=conv_id,
                tool_call_id=tool_call_id,
                originator_open_id=originator_open_id,
                tool_name=tool_name,
            ),
        ),
        ensure_ascii=False,
    )


def build_countdown_card(
    *,
    conv_id: str,
    tool_call_id: str,
    originator_open_id: str,
    tool_name: str,
    description: str,
    remaining_seconds: int,
) -> str:
    template = "red" if remaining_seconds <= 10 else "orange"
    return json.dumps(
        _card_skeleton(
            header_template=template,
            header_title="🛡 Tool Approval Required",
            body_text=_pending_body(tool_name, description, remaining_seconds),
            buttons=_approval_buttons(
                conv_id=conv_id,
                tool_call_id=tool_call_id,
                originator_open_id=originator_open_id,
                tool_name=tool_name,
            ),
        ),
        ensure_ascii=False,
    )


def build_resolved_card(
    *,
    tool_name: str,
    decision: str,
    operator_open_id: str | None = None,
) -> str:
    """Build a terminal-state card (no buttons) shown after the user decides
    or the timer expires.

    ``decision`` is one of ``approve`` / ``deny`` / ``timeout``.
    """
    if decision == "approve":
        template = "green"
        title = "✅ Approved"
        body_text = f"Tool `{tool_name}` was **approved**."
    elif decision == "deny":
        template = "red"
        title = "🚫 Denied"
        body_text = f"Tool `{tool_name}` was **denied**."
    else:
        template = "grey"
        title = "⌛ Timed Out"
        body_text = f"Tool `{tool_name}` approval expired (auto-denied)."

    if operator_open_id:
        body_text += f"\n\n_by_ `{operator_open_id}`"

    return json.dumps(
        _card_skeleton(
            header_template=template,
            header_title=title,
            body_text=body_text,
            buttons=None,
        ),
        ensure_ascii=False,
    )


def _approval_buttons(
    *,
    conv_id: str,
    tool_call_id: str,
    originator_open_id: str,
    tool_name: str,
) -> list[dict[str, Any]]:
    base_value: dict[str, Any] = {
        "type": APPROVAL_ACTION_TYPE,
        "conv_id": conv_id,
        "tool_call_id": tool_call_id,
        "originator_open_id": originator_open_id,
        "tool_name": tool_name,
    }
    return [
        {
            "tag": "button",
            "name": f"approve_{tool_call_id}",
            "text": {"tag": "plain_text", "content": "✅ Approve"},
            "type": "primary_filled",
            "size": "medium",
            "width": "default",
            "behaviors": [
                {
                    "type": "callback",
                    "value": {**base_value, "decision": "approve"},
                }
            ],
        },
        {
            "tag": "button",
            "name": f"deny_{tool_call_id}",
            "text": {"tag": "plain_text", "content": "❌ Deny"},
            "type": "danger_filled",
            "size": "medium",
            "width": "default",
            "behaviors": [
                {
                    "type": "callback",
                    "value": {**base_value, "decision": "deny"},
                }
            ],
        },
    ]


def _pending_body(tool_name: str, description: str, remaining_seconds: int) -> str:
    return f"**Tool:** `{tool_name}`\n{description}\n\n⏱ Expires in **{remaining_seconds}s**"


def _card_skeleton(
    *,
    header_template: str,
    header_title: str,
    body_text: str,
    buttons: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    elements: list[dict[str, Any]] = [
        {"tag": "markdown", "content": body_text},
    ]
    if buttons:
        elements.append({"tag": "hr"})
        elements.append(
            {
                "tag": "column_set",
                "horizontal_spacing": "8px",
                "columns": [
                    {
                        "tag": "column",
                        "width": "weighted",
                        "weight": 1,
                        "elements": [button],
                    }
                    for button in buttons
                ],
            }
        )

    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "template": header_template,
            "title": {"tag": "plain_text", "content": header_title},
        },
        "body": {
            "direction": "vertical",
            "padding": "12px 12px 12px 12px",
            "elements": elements,
        },
    }
