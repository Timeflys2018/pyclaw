from __future__ import annotations

import json

from pyclaw.channels.feishu.approval_card import (
    APPROVAL_ACTION_TYPE,
    build_approval_card,
    build_countdown_card,
    build_resolved_card,
)


class TestApprovalCardSchema:
    def test_card_schema_v2(self) -> None:
        card = json.loads(
            build_approval_card(
                conv_id="c1",
                tool_call_id="x1",
                originator_open_id="ou_a",
                tool_name="bash",
                description="ls -la",
                countdown_seconds=60,
            )
        )
        assert card["schema"] == "2.0"
        assert card["config"]["update_multi"] is True

    def test_buttons_carry_routing_value(self) -> None:
        card = json.loads(
            build_approval_card(
                conv_id="c1",
                tool_call_id="x1",
                originator_open_id="ou_a",
                tool_name="bash",
                description="ls -la",
                countdown_seconds=60,
            )
        )
        column_set = next(
            b for b in card["body"]["elements"] if b.get("tag") == "column_set"
        )
        approve_btn = column_set["columns"][0]["elements"][0]
        deny_btn = column_set["columns"][1]["elements"][0]

        assert approve_btn["tag"] == "button"
        assert approve_btn["name"], "V2 buttons require a non-empty name field"
        assert approve_btn["text"]["content"].endswith("Approve")
        approve_value = approve_btn["behaviors"][0]["value"]
        assert approve_btn["behaviors"][0]["type"] == "callback"
        assert approve_value["type"] == APPROVAL_ACTION_TYPE
        assert approve_value["conv_id"] == "c1"
        assert approve_value["tool_call_id"] == "x1"
        assert approve_value["originator_open_id"] == "ou_a"
        assert approve_value["decision"] == "approve"

        assert deny_btn["name"], "V2 buttons require a non-empty name field"
        deny_value = deny_btn["behaviors"][0]["value"]
        assert deny_value["decision"] == "deny"

    def test_no_deprecated_action_wrapper(self) -> None:
        card = json.loads(
            build_approval_card(
                conv_id="c1",
                tool_call_id="x1",
                originator_open_id="ou_a",
                tool_name="bash",
                description="d",
                countdown_seconds=60,
            )
        )
        for el in card["body"]["elements"]:
            assert el.get("tag") != "action", (
                "Card uses deprecated v1 'tag: action' wrapper — Feishu V2 rejects this"
            )

    def test_countdown_text_in_body(self) -> None:
        card = json.loads(
            build_approval_card(
                conv_id="c",
                tool_call_id="x",
                originator_open_id="o",
                tool_name="bash",
                description="d",
                countdown_seconds=42,
            )
        )
        md = next(e for e in card["body"]["elements"] if e.get("tag") == "markdown")
        assert "42s" in md["content"]
        assert "bash" in md["content"]


class TestCountdownCard:
    def test_red_when_critical(self) -> None:
        card = json.loads(
            build_countdown_card(
                conv_id="c",
                tool_call_id="x",
                originator_open_id="o",
                tool_name="bash",
                description="d",
                remaining_seconds=5,
            )
        )
        assert card["header"]["template"] == "red"

    def test_orange_when_normal(self) -> None:
        card = json.loads(
            build_countdown_card(
                conv_id="c",
                tool_call_id="x",
                originator_open_id="o",
                tool_name="bash",
                description="d",
                remaining_seconds=30,
            )
        )
        assert card["header"]["template"] == "orange"


class TestResolvedCard:
    def test_approve_terminal(self) -> None:
        card = json.loads(
            build_resolved_card(
                tool_name="bash",
                decision="approve",
                operator_open_id="ou_a",
            )
        )
        assert card["header"]["template"] == "green"
        assert "Approved" in card["header"]["title"]["content"]
        for el in card["body"]["elements"]:
            assert el.get("tag") not in ("action", "column_set"), (
                f"Resolved card should have no action surface, got {el.get('tag')}"
            )

    def test_deny_terminal(self) -> None:
        card = json.loads(
            build_resolved_card(
                tool_name="bash",
                decision="deny",
            )
        )
        assert card["header"]["template"] == "red"
        assert "Denied" in card["header"]["title"]["content"]

    def test_timeout_terminal(self) -> None:
        card = json.loads(
            build_resolved_card(
                tool_name="bash",
                decision="timeout",
            )
        )
        assert card["header"]["template"] == "grey"
        assert "Timed Out" in card["header"]["title"]["content"]
