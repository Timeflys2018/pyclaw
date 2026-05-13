from __future__ import annotations

import pytest

from pyclaw.channels.web.chat import _dispatch_protocol_op
from pyclaw.channels.web.protocol import ChatSendMessage


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    async def __call__(self, name: str, *args, **kwargs) -> None:
        self.calls.append((name, args, kwargs))


@pytest.fixture
def patched_handlers(monkeypatch):
    rec = _Recorder()

    async def stop_stub(state, conversation_id):
        await rec("stop", state, conversation_id)

    async def steer_stub(state, conversation_id, args):
        await rec("steer", state, conversation_id, args)

    async def btw_stub(state, conversation_id, args):
        await rec("btw", state, conversation_id, args)

    from pyclaw.channels.web import protocol_ops

    monkeypatch.setattr(protocol_ops, "handle_stop_command", stop_stub)
    monkeypatch.setattr(protocol_ops, "handle_steer_command", steer_stub)
    monkeypatch.setattr(protocol_ops, "handle_btw_command", btw_stub)
    return rec


def _msg(content: str) -> ChatSendMessage:
    return ChatSendMessage(conversation_id="conv_x", content=content)


@pytest.mark.asyncio
async def test_dispatch_stop(patched_handlers):
    await _dispatch_protocol_op(object(), _msg("/stop"))
    assert patched_handlers.calls[0][0] == "stop"


@pytest.mark.asyncio
async def test_dispatch_steer_with_mixed_case_preserves_args(patched_handlers):
    await _dispatch_protocol_op(object(), _msg("/STEER Hello World"))
    assert patched_handlers.calls[0][0] == "steer"
    assert patched_handlers.calls[0][1][2] == "Hello World"


@pytest.mark.asyncio
async def test_dispatch_steer_bare(patched_handlers):
    await _dispatch_protocol_op(object(), _msg("/steer"))
    assert patched_handlers.calls[0][0] == "steer"
    assert patched_handlers.calls[0][1][2] == ""


@pytest.mark.asyncio
async def test_dispatch_btw_preserves_args(patched_handlers):
    await _dispatch_protocol_op(object(), _msg("/btw what is foo"))
    assert patched_handlers.calls[0][0] == "btw"
    assert patched_handlers.calls[0][1][2] == "what is foo"


@pytest.mark.asyncio
async def test_dispatch_steer_with_newline_separator_preserves_args(patched_handlers):
    """Adversarial Invariant 10: multi-line textarea paste lands in steer handler."""
    await _dispatch_protocol_op(object(), _msg("/steer\nactually use X"))
    assert patched_handlers.calls[0][0] == "steer"
    assert patched_handlers.calls[0][1][2] == "actually use X"


@pytest.mark.asyncio
async def test_dispatch_unrecognized_protocol_op_emits_diagnostic(monkeypatch):
    """A protocol_op that doesn't match any branch should log + reply error, not silently dispatch /stop."""
    captured: list[dict] = []

    async def fake_send(state, event_type, conversation_id, payload):
        captured.append(payload)

    from pyclaw.channels.web import chat as chat_mod

    monkeypatch.setattr(chat_mod, "send_event", fake_send)

    msg = _msg("/future_unknown_command")
    await _dispatch_protocol_op(object(), msg)

    assert captured, "Should emit a diagnostic reply"
    assert "未识别" in captured[0]["final_message"] or "unrecognized" in captured[0]["final_message"].lower()
