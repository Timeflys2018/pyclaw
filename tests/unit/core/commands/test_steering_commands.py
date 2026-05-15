from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyclaw.core.agent.run_control import RunControl, SteerMessage
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.steering import (
    CAP_CHARS,
    CAP_COUNT,
    cmd_btw,
    cmd_steer,
)


class _FakeQueueRegistry:
    def __init__(self, rc: RunControl | None) -> None:
        self._rc = rc

    def get_run_control(self, key: str) -> RunControl | None:
        return self._rc


@dataclass
class _Replies:
    texts: list[str]

    async def __call__(self, text: str) -> None:
        self.texts.append(text)


def _make_ctx(
    channel: str = "feishu",
    rc: RunControl | None = None,
    conversation_id: str | None = None,
) -> tuple[CommandContext, _Replies]:
    replies = _Replies(texts=[])
    queue_registry = _FakeQueueRegistry(rc) if channel == "feishu" else None
    session_queue = _FakeQueueRegistry(rc) if channel == "web" else None
    raw: dict[str, object] = {}
    if conversation_id is not None:
        raw["conversation_id"] = conversation_id
    ctx = CommandContext(
        session_id="sess_a",
        session_key="key_a",
        workspace_id="ws",
        user_id="u",
        channel=channel,
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        reply=replies,
        dispatch_user_message=lambda _text: None,  # type: ignore
        raw=raw,
        settings=MagicMock(),
        queue_registry=queue_registry,
        session_queue=session_queue,
    )
    return ctx, replies


@pytest.mark.asyncio
async def test_cmd_steer_active_rc_appends_and_confirms():
    rc = RunControl()
    rc.active = True
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_steer("actually use Python 3.11", ctx)

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].kind == "steer"
    assert rc.pending_steers[0].text == "actually use Python 3.11"
    assert any("已接收" in t or "accepted" in t.lower() for t in replies.texts)


@pytest.mark.asyncio
async def test_cmd_btw_active_rc_appends_sidebar():
    rc = RunControl()
    rc.active = True
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_btw("what is Redis?", ctx)

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].kind == "sidebar"
    assert rc.pending_steers[0].text == "what is Redis?"


@pytest.mark.asyncio
async def test_cmd_steer_inactive_rc_warns_and_does_not_append():
    rc = RunControl()
    assert not rc.is_active()
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_steer("anything", ctx)

    assert rc.pending_steers == []
    assert any("没有正在运行" in t or "no active" in t.lower() for t in replies.texts)


@pytest.mark.asyncio
async def test_cmd_steer_empty_args_shows_usage():
    rc = RunControl()
    rc.active = True
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_steer("", ctx)

    assert rc.pending_steers == []
    assert any("/steer <message>" in t or "需要参数" in t for t in replies.texts)


@pytest.mark.asyncio
async def test_cmd_btw_empty_args_shows_usage():
    rc = RunControl()
    rc.active = True
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_btw("   ", ctx)

    assert rc.pending_steers == []
    assert any("/btw <question>" in t or "需要参数" in t for t in replies.texts)


@pytest.mark.asyncio
async def test_cmd_steer_cap_count_drops_oldest():
    rc = RunControl()
    rc.active = True
    for i in range(CAP_COUNT):
        rc.pending_steers.append(SteerMessage(kind="steer", text=f"msg{i}"))
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_steer("new_msg", ctx)

    assert len(rc.pending_steers) == CAP_COUNT
    assert rc.pending_steers[-1].text == "new_msg"
    assert rc.pending_steers[0].text == "msg1"
    assert any("buffer" in t.lower() or "丢弃" in t or "满" in t for t in replies.texts)


@pytest.mark.asyncio
async def test_cmd_steer_single_message_over_cap_rejected():
    rc = RunControl()
    rc.active = True
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    huge = "x" * (CAP_CHARS + 1)
    await cmd_steer(huge, ctx)

    assert rc.pending_steers == []
    assert any(str(CAP_CHARS) in t or "超过" in t for t in replies.texts)


@pytest.mark.asyncio
async def test_cmd_steer_char_cap_multi_drop_required():
    """Adversarial review Invariant 3: buffer 4 x 499 chars + new 1999 chars = 4 drops needed."""
    rc = RunControl()
    rc.active = True
    for _ in range(4):
        rc.pending_steers.append(SteerMessage(kind="steer", text="x" * 499))
    assert sum(len(m.text) for m in rc.pending_steers) == 1996
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    new_text = "y" * 1999
    await cmd_steer(new_text, ctx)

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].text == new_text
    assert sum(len(m.text) for m in rc.pending_steers) == 1999


@pytest.mark.asyncio
async def test_cmd_steer_char_cap_single_drop_sufficient():
    rc = RunControl()
    rc.active = True
    for _ in range(4):
        rc.pending_steers.append(SteerMessage(kind="steer", text="x" * 500))
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_steer("yy", ctx)

    assert len(rc.pending_steers) == 4
    assert rc.pending_steers[-1].text == "yy"


@pytest.mark.asyncio
async def test_cmd_steer_boundary_exactly_cap_chars_accepted():
    rc = RunControl()
    rc.active = True
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    msg = "x" * CAP_CHARS
    await cmd_steer(msg, ctx)

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].text == msg


@pytest.mark.asyncio
async def test_cmd_steer_4x499_plus_4char_accepted_no_drop():
    """4*499=1996 + 4 chars = exactly 2000 = under strict > cap, accepted."""
    rc = RunControl()
    rc.active = True
    for _ in range(4):
        rc.pending_steers.append(SteerMessage(kind="steer", text="x" * 499))
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_steer("yyyy", ctx)

    assert len(rc.pending_steers) == 5
    assert rc.pending_steers[-1].text == "yyyy"
    assert sum(len(m.text) for m in rc.pending_steers) == CAP_CHARS


@pytest.mark.asyncio
async def test_cmd_steer_4x500_plus_2char_triggers_char_cap_drop():
    """Adversarial Invariant 3 boundary: 4x500=2000 already at cap, +2 chars exceeds."""
    rc = RunControl()
    rc.active = True
    for _ in range(4):
        rc.pending_steers.append(SteerMessage(kind="steer", text="x" * 500))
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_steer("yy", ctx)

    assert len(rc.pending_steers) == 4
    assert rc.pending_steers[-1].text == "yy"
    assert sum(len(m.text) for m in rc.pending_steers) == 1502


@pytest.mark.asyncio
async def test_cmd_steer_mixed_kinds_share_buffer_and_cap():
    rc = RunControl()
    rc.active = True
    rc.pending_steers.extend(
        [
            SteerMessage(kind="steer", text="s1"),
            SteerMessage(kind="steer", text="s2"),
            SteerMessage(kind="sidebar", text="b1"),
            SteerMessage(kind="sidebar", text="b2"),
            SteerMessage(kind="steer", text="s3"),
        ]
    )
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_steer("s4", ctx)

    assert len(rc.pending_steers) == CAP_COUNT
    assert rc.pending_steers[0].text == "s2"
    assert rc.pending_steers[-1].text == "s4"


@pytest.mark.asyncio
async def test_cmd_steer_web_channel_uses_session_queue():
    rc = RunControl()
    rc.active = True
    ctx, replies = _make_ctx(channel="web", rc=rc, conversation_id="conv_123")

    await cmd_steer("from web", ctx)

    assert len(rc.pending_steers) == 1
    assert rc.pending_steers[0].text == "from web"


@pytest.mark.asyncio
async def test_cmd_steer_no_queue_registry_errors_gracefully():
    ctx, replies = _make_ctx(channel="feishu", rc=None)
    ctx.queue_registry = None
    ctx.session_queue = None

    await cmd_steer("anything", ctx)

    assert any("无法" in t or "unable" in t.lower() for t in replies.texts)


@pytest.mark.asyncio
async def test_cmd_steer_strips_whitespace_args():
    rc = RunControl()
    rc.active = True
    ctx, replies = _make_ctx(channel="feishu", rc=rc)

    await cmd_steer("  actually use X  ", ctx)

    assert rc.pending_steers[0].text == "actually use X"
