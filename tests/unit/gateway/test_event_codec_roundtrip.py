from __future__ import annotations

import pytest

from pyclaw.gateway.event_codec import reconstruct_feishu_event


@pytest.fixture
def feishu_payload() -> dict:
    return {
        "schema": "2.0",
        "header": {
            "event_id": "abc123",
            "event_type": "im.message.receive_v1",
            "app_id": "cli_xxx",
        },
        "event": {
            "sender": {
                "sender_id": {"open_id": "ou_test_user"},
                "sender_type": "user",
            },
            "message": {
                "message_id": "om_test_msg",
                "chat_id": "oc_test_chat",
                "chat_type": "p2p",
                "message_type": "text",
                "create_time": "1234567890",
                "content": '{"text":"hello"}',
            },
        },
    }


class TestSerializeRoundTrip:
    def test_lark_oapi_event_round_trip_via_serialize(self, feishu_payload: dict) -> None:
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

        from pyclaw.channels.feishu.handler import serialize_event

        original = P2ImMessageReceiveV1(feishu_payload)
        wire = serialize_event(original)

        assert wire["type"] == "feishu_event"
        assert isinstance(wire["payload"], dict), (
            "regression: serialize_event used to produce a string payload "
            "via json.dumps(default=str), which broke ForwardConsumer "
            "reconstruction"
        )
        assert "event" in wire["payload"]
        assert "header" in wire["payload"]

        reconstructed = reconstruct_feishu_event(wire["payload"])

        assert reconstructed.event.message.message_id == "om_test_msg"
        assert reconstructed.event.message.chat_type == "p2p"
        assert reconstructed.event.sender.sender_id.open_id == "ou_test_user"
        assert reconstructed.header.event_id == "abc123"

    def test_handler_can_call_build_session_key_on_reconstructed(
        self, feishu_payload: dict
    ) -> None:
        from lark_oapi.api.im.v1.model.p2_im_message_receive_v1 import P2ImMessageReceiveV1

        from pyclaw.channels.feishu.handler import build_session_key, serialize_event

        original = P2ImMessageReceiveV1(feishu_payload)
        wire = serialize_event(original)
        reconstructed = reconstruct_feishu_event(wire["payload"])

        sk_orig = build_session_key("cli_xxx", original, "chat")
        sk_recon = build_session_key("cli_xxx", reconstructed, "chat")

        assert sk_orig == sk_recon
        assert sk_orig == "feishu:cli_xxx:ou_test_user"


class TestSerializeNonPydanticObject:
    def test_to_jsonable_handles_nested_objects(self) -> None:
        from pyclaw.channels.feishu.handler import _to_jsonable

        class Inner:
            def __init__(self) -> None:
                self.val = 42
                self._private = "hidden"

        class Outer:
            def __init__(self) -> None:
                self.inner = Inner()
                self.items = [1, 2, 3]

        result = _to_jsonable(Outer())
        assert result == {"inner": {"val": 42}, "items": [1, 2, 3]}
        assert "_private" not in result["inner"]

    def test_to_jsonable_handles_dict_with_non_str_keys(self) -> None:
        from pyclaw.channels.feishu.handler import _to_jsonable

        result = _to_jsonable({1: "a", "b": 2})
        assert result == {"1": "a", "b": 2}

    def test_to_jsonable_handles_bytes(self) -> None:
        from pyclaw.channels.feishu.handler import _to_jsonable

        result = _to_jsonable(b"hello")
        assert result == "hello"
