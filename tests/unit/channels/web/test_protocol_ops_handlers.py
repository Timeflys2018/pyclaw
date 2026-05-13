from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pyclaw.channels.web.protocol import SERVER_CHAT_DONE
from pyclaw.channels.web.protocol_ops import (
    handle_btw_command,
    handle_steer_command,
)
from pyclaw.core.agent.run_control import RunControl


class _FakeSessionQueue:
    def __init__(self, rc: RunControl) -> None:
        self._rc = rc

    def get_run_control(self, conversation_id: str) -> RunControl:
        return self._rc


def _state_with_queue(rc: RunControl) -> MagicMock:
    state = MagicMock()
    state.ws = MagicMock()
    fake_queue = _FakeSessionQueue(rc)
    state._test_fake_queue = fake_queue
    return state


@pytest.fixture
def patched_get_session_queue(monkeypatch):
    from pyclaw.channels.web import chat as chat_mod

    def fake_get_queue(state):
        return state._test_fake_queue

    monkeypatch.setattr(chat_mod, "_get_session_queue", fake_get_queue)


@pytest.fixture
def patched_send_event(monkeypatch):
    captured: list[dict] = []

    async def fake_send(state, event_type, conversation_id, payload):
        captured.append({
            "event_type": event_type,
            "conversation_id": conversation_id,
            "payload": payload,
        })

    from pyclaw.channels.web import protocol_ops

    monkeypatch.setattr(protocol_ops, "send_event", fake_send)
    return captured


@pytest.mark.asyncio
async def test_handle_steer_command_active_appends(
    patched_get_session_queue, patched_send_event
):
    rc = RunControl()
    rc.active = True
    state = _state_with_queue(rc)

    await handle_steer_command(state, "conv_1", "actually use X")

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].kind == "steer"
    assert rc.pending_steers[0].text == "actually use X"
    assert len(patched_send_event) == 1
    event = patched_send_event[0]
    assert event["event_type"] == SERVER_CHAT_DONE
    assert event["conversation_id"] == "conv_1"
    assert "已接收" in event["payload"]["final_message"]


@pytest.mark.asyncio
async def test_handle_steer_command_inactive_warns(
    patched_get_session_queue, patched_send_event
):
    rc = RunControl()
    state = _state_with_queue(rc)

    await handle_steer_command(state, "conv_1", "anything")

    assert rc.pending_steers == []
    assert len(patched_send_event) == 1
    assert "没有正在运行" in patched_send_event[0]["payload"]["final_message"]


@pytest.mark.asyncio
async def test_handle_steer_command_empty_args_shows_usage(
    patched_get_session_queue, patched_send_event
):
    rc = RunControl()
    rc.active = True
    state = _state_with_queue(rc)

    await handle_steer_command(state, "conv_1", "")

    assert rc.pending_steers == []
    assert any(
        "/steer <message>" in ev["payload"]["final_message"]
        or "需要参数" in ev["payload"]["final_message"]
        for ev in patched_send_event
    )


@pytest.mark.asyncio
async def test_handle_btw_command_active_appends(
    patched_get_session_queue, patched_send_event
):
    rc = RunControl()
    rc.active = True
    state = _state_with_queue(rc)

    await handle_btw_command(state, "conv_1", "what about Y")

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].kind == "sidebar"
    assert rc.pending_steers[0].text == "what about Y"


@pytest.mark.asyncio
async def test_handle_btw_command_empty_args_shows_usage(
    patched_get_session_queue, patched_send_event
):
    rc = RunControl()
    rc.active = True
    state = _state_with_queue(rc)

    await handle_btw_command(state, "conv_1", "")

    assert rc.pending_steers == []
    assert any(
        "/btw <question>" in ev["payload"]["final_message"]
        or "需要参数" in ev["payload"]["final_message"]
        for ev in patched_send_event
    )
