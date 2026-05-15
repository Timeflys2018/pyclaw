from __future__ import annotations

from pyclaw.channels.web.protocol import ChatSendMessage, parse_client_message


class TestChatSendTierField:
    def test_no_tier_field_defaults_to_none(self) -> None:
        msg = parse_client_message(
            {
                "type": "chat.send",
                "conversation_id": "c1",
                "content": "hi",
            }
        )
        assert isinstance(msg, ChatSendMessage)
        assert msg.tier is None

    def test_valid_tier_read_only(self) -> None:
        msg = parse_client_message(
            {
                "type": "chat.send",
                "conversation_id": "c1",
                "content": "hi",
                "tier": "read-only",
            }
        )
        assert isinstance(msg, ChatSendMessage)
        assert msg.tier == "read-only"

    def test_valid_tier_approval(self) -> None:
        msg = parse_client_message(
            {
                "type": "chat.send",
                "conversation_id": "c1",
                "content": "hi",
                "tier": "approval",
            }
        )
        assert isinstance(msg, ChatSendMessage)
        assert msg.tier == "approval"

    def test_valid_tier_yolo(self) -> None:
        msg = parse_client_message(
            {
                "type": "chat.send",
                "conversation_id": "c1",
                "content": "hi",
                "tier": "yolo",
            }
        )
        assert isinstance(msg, ChatSendMessage)
        assert msg.tier == "yolo"

    def test_invalid_tier_value_rejected(self) -> None:
        result = parse_client_message(
            {
                "type": "chat.send",
                "conversation_id": "c1",
                "content": "hi",
                "tier": "bogus",
            }
        )
        assert result is None

    def test_invalid_tier_uppercase_rejected(self) -> None:
        result = parse_client_message(
            {
                "type": "chat.send",
                "conversation_id": "c1",
                "content": "hi",
                "tier": "READ-ONLY",
            }
        )
        assert result is None

    def test_tier_field_only_validated_for_chat_send(self) -> None:
        msg = parse_client_message(
            {
                "type": "tool.approve",
                "conversation_id": "c1",
                "tool_call_id": "x",
                "approved": True,
                "tier": "bogus",
            }
        )
        assert msg is not None

    def test_explicit_tier_none_treated_as_omitted(self) -> None:
        msg = parse_client_message(
            {
                "type": "chat.send",
                "conversation_id": "c1",
                "content": "hi",
                "tier": None,
            }
        )
        assert isinstance(msg, ChatSendMessage)
        assert msg.tier is None
