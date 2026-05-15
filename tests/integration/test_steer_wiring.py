"""Integration wiring test for /steer and /btw (REQUIRED per Case 8 methodology).

Exercises the real classify -> dispatch -> RunControl -> SteerHook -> build_per_turn_suffix chain
with real classes (no mocks in the middle). Only the WebSocket event send path is stubbed
to capture outgoing events for assertions.

Scenarios per tasks.md phase 9:
 1. Web path: classify -> _dispatch_protocol_op -> handle_steer_command -> rc.pending_steers append.
 2. Web hook drain: SteerHook registered in HookRegistry, build_per_turn_suffix drains + renders <user_steer>.
 3. Feishu path: handle_feishu_message with /steer ... -> rc.pending_steers append (no FeishuCommandAdapter).
 4. End-to-end: pre-populated pending_steers drained in one iteration via the full build_per_turn_suffix.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.web.chat import SessionQueue, _dispatch_protocol_op
from pyclaw.channels.web.message_classifier import classify
from pyclaw.channels.web.protocol import ChatSendMessage
from pyclaw.core.agent.hooks.steer_hook import SteerHook
from pyclaw.core.agent.run_control import RunControl
from pyclaw.core.agent.system_prompt import PromptInputs, build_per_turn_suffix
from pyclaw.core.hooks import HookRegistry


class _CapturedEvents:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def __call__(self, state, event_type, conversation_id, payload):
        self.events.append(
            {"event_type": event_type, "conversation_id": conversation_id, "payload": payload}
        )


def _install_rc_on_module_queue(rc: RunControl, conversation_id: str) -> SessionQueue:
    """Attach RunControl to the module-level SessionQueue singleton that _get_session_queue returns."""
    from pyclaw.channels.web import chat as chat_mod

    queue = chat_mod._session_queue  # noqa: SLF001
    queue._run_controls[conversation_id] = rc  # noqa: SLF001
    return queue


def _state_with_real_queue(rc: RunControl) -> tuple[MagicMock, SessionQueue]:
    queue = _install_rc_on_module_queue(rc, "conv_web_1")
    state = MagicMock()
    state.ws = MagicMock()
    return state, queue


@pytest.mark.asyncio
async def test_web_classify_then_dispatch_steer_appends_to_real_run_control(monkeypatch):
    """Scenario 1: Web path uses REAL classify, REAL _dispatch_protocol_op, REAL SessionQueue."""
    rc = RunControl()
    rc.active = True
    state, queue = _state_with_real_queue(rc)

    captured = _CapturedEvents()
    from pyclaw.channels.web import chat as chat_mod
    from pyclaw.channels.web import protocol_ops as ops_mod

    monkeypatch.setattr(ops_mod, "send_event", captured)
    monkeypatch.setattr(chat_mod, "send_event", captured)

    msg = ChatSendMessage(
        conversation_id="conv_web_1",
        content="/steer actually use Python 3.11",
    )

    assert classify(msg.content) == "protocol_op"

    await _dispatch_protocol_op(state, msg)

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].kind == "steer"
    assert rc.pending_steers[0].text == "actually use Python 3.11"
    assert len(captured.events) == 1
    assert "已接收" in captured.events[0]["payload"]["final_message"]


@pytest.mark.asyncio
async def test_steer_hook_drains_and_renders_in_build_per_turn_suffix():
    """Scenario 2: REAL SteerHook in REAL HookRegistry, REAL build_per_turn_suffix renders <user_steer>."""
    from pyclaw.core.agent.run_control import SteerMessage

    rc = RunControl()
    hook = SteerHook()
    await hook.on_run_start("sess_x", rc)

    rc.pending_steers.append(SteerMessage(kind="steer", text="use Redis cache"))
    rc.pending_steers.append(SteerMessage(kind="sidebar", text="what is X?"))

    registry = HookRegistry()
    registry.register(hook)

    inputs = PromptInputs(
        session_id="sess_x",
        workspace_id="ws",
        agent_id="agent",
        model="claude-sonnet-4",
        tools=(("bash", "Run a shell command"),),
    )

    result = await build_per_turn_suffix(inputs, hooks=registry, user_prompt="task")

    assert "<user_steer>" in result.text
    assert "- use Redis cache" in result.text
    assert "</user_steer>" in result.text
    assert "<user_sidebar>" in result.text
    assert "- what is X?" in result.text
    assert "briefly" in result.text.lower()

    assert rc.pending_steers == [], "Drain must empty the buffer"


@pytest.mark.asyncio
async def test_steer_hook_empty_buffer_produces_no_injection():
    """Scenario 2b: empty buffer means SteerHook contributes nothing to the prompt."""
    rc = RunControl()
    hook = SteerHook()
    await hook.on_run_start("sess_y", rc)

    registry = HookRegistry()
    registry.register(hook)

    inputs = PromptInputs(
        session_id="sess_y",
        workspace_id="ws",
        agent_id="agent",
        model="claude-sonnet-4",
    )
    result = await build_per_turn_suffix(inputs, hooks=registry)

    assert "<user_steer>" not in result.text
    assert "<user_sidebar>" not in result.text


@pytest.mark.asyncio
async def test_feishu_path_handle_steer_feishu_appends_without_command_adapter():
    """Scenario 3: Feishu path bypasses FeishuCommandAdapter for /steer."""
    from pyclaw.channels.feishu.handler import handle_steer_feishu

    rc = RunControl()
    rc.active = True
    queue_registry = type(
        "_Reg",
        (),
        {"get_run_control": lambda self, sid: rc},
    )()

    ctx = AsyncMock()
    ctx.queue_registry = queue_registry
    ctx.feishu_client = AsyncMock()

    await handle_steer_feishu(ctx, "sess_feishu_1", "msg_abc", "use X")

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].kind == "steer"
    assert rc.pending_steers[0].text == "use X"
    ctx.feishu_client.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_end_to_end_one_iteration_cycle():
    """Scenario 4: pre-populated pending_steers -> build_per_turn_suffix -> assembled system prompt contains XML."""
    from pyclaw.core.agent.run_control import SteerMessage

    rc = RunControl()
    hook = SteerHook()
    await hook.on_run_start("sess_e2e", rc)

    rc.pending_steers.extend(
        [
            SteerMessage(kind="steer", text="e2e_steer"),
            SteerMessage(kind="sidebar", text="e2e_side"),
        ]
    )
    assert len(rc.pending_steers) == 2

    registry = HookRegistry()
    registry.register(hook)

    inputs = PromptInputs(
        session_id="sess_e2e",
        workspace_id="ws",
        agent_id="agent",
        model="claude-sonnet-4",
    )
    result = await build_per_turn_suffix(inputs, hooks=registry, user_prompt="main task")

    assert "<user_steer>" in result.text
    assert "e2e_steer" in result.text
    assert "<user_sidebar>" in result.text
    assert "e2e_side" in result.text

    assert rc.pending_steers == []

    result2 = await build_per_turn_suffix(inputs, hooks=registry, user_prompt="main task")
    assert "<user_steer>" not in result2.text
    assert "<user_sidebar>" not in result2.text


@pytest.mark.asyncio
async def test_multi_line_steer_from_web_textarea_is_classified_as_protocol_op(monkeypatch):
    """Regression: Adversarial Invariant 10 — newline separator misclassification would have defeated mid-run semantic."""
    rc = RunControl()
    rc.active = True
    state, queue = _state_with_real_queue(rc)

    captured = _CapturedEvents()
    from pyclaw.channels.web import chat as chat_mod
    from pyclaw.channels.web import protocol_ops as ops_mod

    monkeypatch.setattr(ops_mod, "send_event", captured)
    monkeypatch.setattr(chat_mod, "send_event", captured)

    msg = ChatSendMessage(
        conversation_id="conv_web_1",
        content="/steer\nactually use the Redis cache",
    )

    assert classify(msg.content) == "protocol_op", (
        "Multi-line /steer must classify as protocol_op (whitespace-class regex)"
    )

    await _dispatch_protocol_op(state, msg)

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].text == "actually use the Redis cache"
