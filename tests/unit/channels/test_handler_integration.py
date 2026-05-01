from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.channels.feishu.dedup import FeishuDedup
from pyclaw.channels.feishu.handler import FeishuContext, handle_feishu_message
from pyclaw.channels.session_router import SessionRouter
from pyclaw.infra.settings import FeishuSettings
from pyclaw.models import Done, TextChunk
from pyclaw.storage.session.base import InMemorySessionStore
from pyclaw.storage.workspace.file import FileWorkspaceStore


def _make_text_event(
    text: str,
    chat_type: str = "p2p",
    open_id: str = "ou_test",
    chat_id: str = "oc_chat1",
    message_id: str = "msg_001",
    mentions: list[Any] | None = None,
) -> Any:
    event = MagicMock()
    event.event.sender.sender_id.open_id = open_id
    event.event.message.chat_type = chat_type
    event.event.message.chat_id = chat_id
    event.event.message.thread_id = ""
    event.event.message.message_type = "text"
    event.event.message.message_id = message_id
    event.event.message.content = f'{{"text": "{text}"}}'
    event.event.message.mentions = mentions or []
    return event


def _make_ctx(store: InMemorySessionStore, tmp_path: Path) -> FeishuContext:
    settings = FeishuSettings(enabled=True, app_id="cli_x", app_secret="s")
    feishu_client = MagicMock()
    feishu_client.reply_text = AsyncMock(return_value=None)
    feishu_client._client = MagicMock()

    from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
    from pyclaw.core.agent.runner import AgentRunnerDeps
    from pyclaw.core.agent.tools.registry import ToolRegistry
    from pyclaw.core.hooks import HookRegistry
    from pyclaw.models import AgentRunConfig

    class _OneShotLLM(LLMClient):
        def __init__(self) -> None:
            super().__init__(default_model="fake")

        async def stream(self, *, messages, model=None, tools=None, system=None,
                         idle_seconds=0.0, abort_event=None):
            yield LLMStreamChunk(text_delta="hello from agent")
            yield LLMStreamChunk(finish_reason="stop", usage=LLMUsage())

    deps = AgentRunnerDeps(
        llm=_OneShotLLM(),
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        session_store=store,
        config=AgentRunConfig(),
    )

    dedup = FeishuDedup()
    workspace_store = FileWorkspaceStore(base_dir=tmp_path / "workspaces")
    router = SessionRouter(store=store)

    return FeishuContext(
        settings=settings,
        feishu_client=feishu_client,
        deps=deps,
        dedup=dedup,
        workspace_store=workspace_store,
        bot_open_id="bot_open_id",
        session_router=router,
        workspace_base=tmp_path / "workspaces",
    )


@pytest.mark.asyncio
async def test_handle_feishu_message_creates_session_and_enqueues(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    event = _make_text_event("你好", message_id="msg_001")

    enqueued = []
    original_enqueue = __import__("pyclaw.channels.feishu.queue", fromlist=["enqueue"]).enqueue

    async def _capture_enqueue(session_id: str, coro) -> None:
        enqueued.append(session_id)
        await coro

    with patch("pyclaw.channels.feishu.handler.enqueue", side_effect=_capture_enqueue):
        await handle_feishu_message(event, ctx)

    assert len(enqueued) == 1
    session_id = enqueued[0]
    assert session_id.startswith("feishu:cli_x:ou_test")

    tree = await store.load(session_id)
    assert tree is not None


@pytest.mark.asyncio
async def test_handle_feishu_message_dedup_skips_duplicate(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    event = _make_text_event("hello", message_id="msg_dup")

    enqueued = []

    async def _capture_enqueue(session_id: str, coro) -> None:
        enqueued.append(session_id)
        await coro

    with patch("pyclaw.channels.feishu.handler.enqueue", side_effect=_capture_enqueue):
        await handle_feishu_message(event, ctx)
        await handle_feishu_message(event, ctx)

    assert len(enqueued) == 1


@pytest.mark.asyncio
async def test_handle_feishu_message_group_no_mention_skipped(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    event = _make_text_event("大家好", chat_type="group", message_id="msg_group_001")

    enqueued = []

    async def _capture_enqueue(session_id: str, coro) -> None:
        enqueued.append(session_id)
        await coro

    with patch("pyclaw.channels.feishu.handler.enqueue", side_effect=_capture_enqueue):
        await handle_feishu_message(event, ctx)

    assert len(enqueued) == 0


@pytest.mark.asyncio
async def test_handle_feishu_message_slash_command_intercepted(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    sid, _ = await ctx.session_router.resolve_or_create("feishu:cli_x:ou_test", "feishu_cli_x_ou_test")
    event = _make_text_event("/help", message_id="msg_help_001")

    enqueued = []

    async def _capture_enqueue(session_id: str, coro) -> None:
        enqueued.append(session_id)
        await coro

    with patch("pyclaw.channels.feishu.handler.enqueue", side_effect=_capture_enqueue):
        await handle_feishu_message(event, ctx)

    assert len(enqueued) == 0
    ctx.feishu_client.reply_text.assert_awaited_once()
    reply = ctx.feishu_client.reply_text.call_args[0][1]
    assert "/new" in reply


@pytest.mark.asyncio
async def test_handle_feishu_message_updates_last_interaction(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    event = _make_text_event("hello update", message_id="msg_update_001")

    async def _run_immediately(session_id: str, coro) -> None:
        await coro

    with patch("pyclaw.channels.feishu.handler.enqueue", side_effect=_run_immediately):
        with patch("pyclaw.channels.feishu.handler._dispatch_and_reply", new_callable=AsyncMock):
            await handle_feishu_message(event, ctx)

    current_sid = await store.get_current_session_id("feishu:cli_x:ou_test")
    assert current_sid is not None
    tree = await store.load(current_sid)
    assert tree is not None
    assert tree.header.last_interaction_at is not None


@pytest.mark.asyncio
async def test_dispatch_and_reply_fallback_on_card_failure(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)

    from pyclaw.channels.base import InboundMessage
    from pyclaw.channels.feishu.handler import _dispatch_and_reply

    sid, _ = await ctx.session_router.resolve_or_create("feishu:cli_x:ou_test", "ws")
    inbound = InboundMessage(
        session_id=sid,
        user_message="test fallback",
        workspace_id="feishu_cli_x_ou_test",
        channel="feishu",
    )

    async def _mock_stream(*args, **kwargs):
        from pyclaw.models import Done
        yield Done(final_message="fallback response", usage={})

    with patch("pyclaw.channels.feishu.handler.dispatch_message", side_effect=_mock_stream):
        with patch("pyclaw.channels.feishu.streaming.FeishuStreamingCard") as mock_card_cls:
            mock_card = MagicMock()
            mock_card.start = AsyncMock(side_effect=RuntimeError("card unavailable"))
            mock_card_cls.return_value = mock_card

            await _dispatch_and_reply(
                inbound, ctx, "msg_fallback_001",
                tmp_path / "ws", extra_system=""
            )

    ctx.feishu_client.reply_text.assert_awaited_once()
    reply_text = ctx.feishu_client.reply_text.call_args[0][1]
    assert "fallback response" in reply_text


@pytest.mark.asyncio
async def test_cmd_new_with_args_rotates_and_enqueues_followup(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    old_sid, _ = await ctx.session_router.resolve_or_create(
        "feishu:cli_x:ou_test", "feishu_cli_x_ou_test"
    )
    event = _make_text_event("/new 写一首诗", message_id="msg_newargs_001")

    all_enqueued: list[tuple[str, Any]] = []

    async def _capture_enqueue(session_id: str, coro) -> None:
        all_enqueued.append((session_id, coro))
        coro.close()

    with patch("pyclaw.channels.feishu.handler.enqueue", side_effect=_capture_enqueue):
        with patch("pyclaw.channels.feishu.queue.enqueue", side_effect=_capture_enqueue):
            await handle_feishu_message(event, ctx)

    new_sid = await store.get_current_session_id("feishu:cli_x:ou_test")
    assert new_sid is not None
    assert new_sid != old_sid

    ctx.feishu_client.reply_text.assert_awaited_once()
    reply = ctx.feishu_client.reply_text.call_args[0][1]
    assert "新会话" in reply

    followup_enqueued = [s for s, _ in all_enqueued if s == new_sid]
    assert len(followup_enqueued) >= 1
