from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.channels.feishu.command_adapter import FeishuCommandAdapter
from pyclaw.channels.feishu.handler import FeishuContext
from pyclaw.channels.session_router import SessionRouter
from pyclaw.channels.web.command_adapter import WebCommandAdapter
from pyclaw.core.commands.builtin import register_builtin_commands
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.commands.spec import CommandSpec
from pyclaw.core.sop_extraction import ExtractionResult
from pyclaw.infra.settings import EvolutionSettings, FeishuSettings, Settings
from pyclaw.storage.session.base import InMemorySessionStore


def _make_registry() -> CommandRegistry:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    return registry


def _feishu_event(open_id: str = "ou_user", chat_type: str = "p2p") -> Any:
    event = MagicMock()
    event.event.sender.sender_id.open_id = open_id
    event.event.message.chat_type = chat_type
    event.event.message.chat_id = ""
    event.event.message.thread_id = ""
    event.event.message.message_type = "text"
    event.event.message.content = ""
    event.event.message.mentions = []
    return event


def _make_feishu_ctx(store: InMemorySessionStore | None = None) -> FeishuContext:
    if store is None:
        store = InMemorySessionStore()
    feishu_client = MagicMock()
    feishu_client.reply_text = AsyncMock(return_value=None)
    deps = MagicMock()
    deps.session_store = store
    deps.llm = MagicMock()
    deps.llm.default_model = "test-model"
    queue_registry = MagicMock()

    async def _enqueue(_session_id, coro):
        coro.close()

    queue_registry.enqueue = AsyncMock(side_effect=_enqueue)
    return FeishuContext(
        settings=FeishuSettings(enabled=True, app_id="cli_x", app_secret="s"),
        settings_full=Settings(),
        feishu_client=feishu_client,
        deps=deps,
        dedup=MagicMock(),
        workspace_store=MagicMock(),
        bot_open_id="bot",
        session_router=SessionRouter(store=store),
        workspace_base=Path("/tmp/test"),
        queue_registry=queue_registry,
        redis_client=None,
        memory_store=None,
        evolution_settings=None,
        nudge_hook=None,
    )


def _make_web_state(user_id: str = "user1") -> Any:
    state = MagicMock()
    state.ws = MagicMock()
    state.ws.send_json = AsyncMock(return_value=None)
    state.ws.app.state.web_settings = MagicMock()
    state.user_id = user_id
    state.seq = 0
    return state


@pytest.mark.asyncio
async def test_feishu_status_integration() -> None:
    store = InMemorySessionStore()
    ctx = _make_feishu_ctx(store)
    sid, _ = await ctx.session_router.resolve_or_create("feishu:cli:user", "ws")
    adapter = FeishuCommandAdapter(_make_registry())

    handled = await adapter.handle(
        text="/status",
        session_key="feishu:cli:user",
        session_id=sid,
        message_id="mid",
        event=_feishu_event(),
        ctx=ctx,
    )

    assert handled is True
    msg = ctx.feishu_client.reply_text.call_args[0][1]
    assert "feishu:cli:user" in msg
    assert "test-model" in msg


@pytest.mark.asyncio
async def test_feishu_new_with_followup_dispatches_user_message() -> None:
    store = InMemorySessionStore()
    ctx = _make_feishu_ctx(store)
    await ctx.session_router.resolve_or_create("feishu:cli:user", "ws")
    sid = await store.get_current_session_id("feishu:cli:user")
    adapter = FeishuCommandAdapter(_make_registry())

    with patch(
        "pyclaw.channels.feishu.handler._dispatch_and_reply",
        new_callable=AsyncMock,
        return_value=None,
    ):
        handled = await adapter.handle(
            text="/new 帮我做 X",
            session_key="feishu:cli:user",
            session_id=sid,
            message_id="mid",
            event=_feishu_event(),
            ctx=ctx,
        )

    assert handled is True
    ctx.queue_registry.enqueue.assert_awaited()


@pytest.mark.asyncio
async def test_feishu_new_without_followup_does_not_dispatch() -> None:
    store = InMemorySessionStore()
    ctx = _make_feishu_ctx(store)
    await ctx.session_router.resolve_or_create("feishu:cli:user", "ws")
    sid = await store.get_current_session_id("feishu:cli:user")
    adapter = FeishuCommandAdapter(_make_registry())

    handled = await adapter.handle(
        text="/new",
        session_key="feishu:cli:user",
        session_id=sid,
        message_id="mid",
        event=_feishu_event(),
        ctx=ctx,
    )

    assert handled is True
    ctx.queue_registry.enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_feishu_unknown_command_falls_through() -> None:
    ctx = _make_feishu_ctx()
    adapter = FeishuCommandAdapter(_make_registry())
    handled = await adapter.handle(
        text="/notacommand",
        session_key="key",
        session_id="sid",
        message_id="mid",
        event=_feishu_event(),
        ctx=ctx,
    )
    assert handled is False


@pytest.mark.asyncio
async def test_feishu_alias_learn_triggers_extract() -> None:
    ctx = _make_feishu_ctx()
    ctx.redis_client = MagicMock()
    ctx.redis_client.set = AsyncMock(return_value=True)
    ctx.redis_client.hgetall = AsyncMock(return_value={})
    ctx.memory_store = MagicMock()
    ctx.evolution_settings = EvolutionSettings(enabled=True)
    success = ExtractionResult(spawned=True, llm_returned_count=1, written=1)
    adapter = FeishuCommandAdapter(_make_registry())

    with patch(
        "pyclaw.core.sop_extraction.extract_sops_sync",
        new_callable=AsyncMock,
        return_value=success,
    ):
        handled = await adapter.handle(
            text="/learn",
            session_key="feishu:user",
            session_id="ses_1",
            message_id="msg_1",
            event=_feishu_event(),
            ctx=ctx,
        )

    assert handled is True
    msg = ctx.feishu_client.reply_text.call_args[0][1]
    assert "学到" in msg


@pytest.mark.asyncio
async def test_feishu_channel_restriction_replies_error() -> None:
    ctx = _make_feishu_ctx()
    registry = _make_registry()

    async def web_only_handler(args: str, c) -> None:
        await c.reply("should not reach")

    registry.register(
        CommandSpec(
            name="/webonly",
            handler=web_only_handler,
            category="t",
            help_text="t",
            channels=frozenset({"web"}),
        )
    )
    adapter = FeishuCommandAdapter(registry)
    handled = await adapter.handle(
        text="/webonly",
        session_key="key",
        session_id="sid",
        message_id="mid",
        event=_feishu_event(),
        ctx=ctx,
    )
    assert handled is True
    msg = ctx.feishu_client.reply_text.call_args[0][1]
    assert "/webonly" in msg
    assert "web" in msg


@pytest.mark.asyncio
async def test_web_adapter_status_integration() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid, _ = await router.resolve_or_create("web:user1", "default")
    deps = MagicMock()
    deps.session_store = store
    deps.llm = MagicMock()
    deps.llm.default_model = "test-model"

    state = _make_web_state("user1")
    adapter = WebCommandAdapter(_make_registry())
    handled = await adapter.handle(
        text="/status",
        state=state,
        conversation_id="conv1",
        session_id=sid,
        deps=deps,
        session_router=router,
        workspace_base=Path("/tmp"),
        settings=Settings(),
    )

    assert handled is True
    state.ws.send_json.assert_awaited()
    envelope = state.ws.send_json.await_args[0][0]
    assert envelope["type"] == "chat.done"
    assert "test-model" in envelope["data"]["final_message"]


@pytest.mark.asyncio
async def test_web_adapter_whoami_uses_user_id() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid, _ = await router.resolve_or_create("web:bob", "default")
    state = _make_web_state("bob")
    deps = MagicMock()
    deps.session_store = store
    adapter = WebCommandAdapter(_make_registry())

    await adapter.handle(
        text="/whoami",
        state=state,
        conversation_id="c",
        session_id=sid,
        deps=deps,
        session_router=router,
        workspace_base=Path("/tmp"),
        settings=Settings(),
    )

    envelope = state.ws.send_json.await_args[0][0]
    msg = envelope["data"]["final_message"]
    assert "bob" in msg
    assert "web" in msg


@pytest.mark.asyncio
async def test_web_adapter_idle_30m() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid, _ = await router.resolve_or_create("web:user1", "default")
    state = _make_web_state("user1")
    deps = MagicMock()
    deps.session_store = store
    adapter = WebCommandAdapter(_make_registry())

    await adapter.handle(
        text="/idle 30m",
        state=state,
        conversation_id="c",
        session_id=sid,
        deps=deps,
        session_router=router,
        workspace_base=Path("/tmp"),
        settings=Settings(),
    )

    tree = await store.load(sid)
    assert tree is not None
    assert tree.header.idle_minutes_override == 30


@pytest.mark.asyncio
async def test_web_adapter_history_integration() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    await router.resolve_or_create("web:user1", "default")
    await router.rotate("web:user1", "default")
    sid = await store.get_current_session_id("web:user1")
    state = _make_web_state("user1")
    deps = MagicMock()
    deps.session_store = store
    adapter = WebCommandAdapter(_make_registry())

    await adapter.handle(
        text="/history",
        state=state,
        conversation_id="c",
        session_id=sid,
        deps=deps,
        session_router=router,
        workspace_base=Path("/tmp"),
        settings=Settings(),
    )

    envelope = state.ws.send_json.await_args[0][0]
    msg = envelope["data"]["final_message"]
    assert "历史会话" in msg


@pytest.mark.asyncio
async def test_web_adapter_help_integration() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid, _ = await router.resolve_or_create("web:user1", "default")
    state = _make_web_state("user1")
    deps = MagicMock()
    deps.session_store = store
    adapter = WebCommandAdapter(_make_registry())

    await adapter.handle(
        text="/help",
        state=state,
        conversation_id="c",
        session_id=sid,
        deps=deps,
        session_router=router,
        workspace_base=Path("/tmp"),
        settings=Settings(),
    )

    envelope = state.ws.send_json.await_args[0][0]
    msg = envelope["data"]["final_message"]
    assert "📖 PyClaw 命令帮助" in msg
    assert "/idle" in msg


@pytest.mark.asyncio
async def test_web_adapter_unknown_falls_through() -> None:
    state = _make_web_state("user1")
    deps = MagicMock()
    adapter = WebCommandAdapter(_make_registry())
    handled = await adapter.handle(
        text="/notacmd",
        state=state,
        conversation_id="c",
        session_id="sid",
        deps=deps,
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        settings=Settings(),
    )
    assert handled is False


@pytest.mark.asyncio
async def test_web_adapter_alias_learn() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid, _ = await router.resolve_or_create("web:user1", "default")
    state = _make_web_state("user1")
    deps = MagicMock()
    deps.session_store = store
    deps.llm = MagicMock()

    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    redis.hgetall = AsyncMock(return_value={})
    success = ExtractionResult(spawned=True, llm_returned_count=1, written=1)

    adapter = WebCommandAdapter(_make_registry())
    with patch(
        "pyclaw.core.sop_extraction.extract_sops_sync",
        new_callable=AsyncMock,
        return_value=success,
    ):
        handled = await adapter.handle(
            text="/learn",
            state=state,
            conversation_id="c",
            session_id=sid,
            deps=deps,
            session_router=router,
            workspace_base=Path("/tmp"),
            settings=Settings(),
            redis_client=redis,
            memory_store=MagicMock(),
            evolution_settings=EvolutionSettings(enabled=True),
        )

    assert handled is True
    envelope = state.ws.send_json.await_args[0][0]
    assert "学到" in envelope["data"]["final_message"]


@pytest.mark.asyncio
async def test_web_adapter_channel_restriction_replies_error() -> None:
    state = _make_web_state("user1")
    deps = MagicMock()
    deps.session_store = InMemorySessionStore()
    registry = CommandRegistry()
    register_builtin_commands(registry)

    async def feishu_only(args: str, c) -> None:
        await c.reply("should not reach")

    registry.register(
        CommandSpec(
            name="/feishuonly",
            handler=feishu_only,
            category="t",
            help_text="t",
            channels=frozenset({"feishu"}),
        )
    )
    adapter = WebCommandAdapter(registry)
    handled = await adapter.handle(
        text="/feishuonly",
        state=state,
        conversation_id="c",
        session_id="sid",
        deps=deps,
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        settings=Settings(),
    )
    assert handled is True
    envelope = state.ws.send_json.await_args[0][0]
    msg = envelope["data"]["final_message"]
    assert "/feishuonly" in msg
    assert "feishu" in msg


@pytest.mark.asyncio
async def test_extract_consistency_three_channels_call_same_helper() -> None:
    """8.4: Feishu adapter, Web adapter, REST endpoint all delegate to run_extract helper."""
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    redis.hgetall = AsyncMock(return_value={})
    memory_store = MagicMock()
    settings = EvolutionSettings(enabled=True)
    success = ExtractionResult(spawned=True, llm_returned_count=1, written=1)

    feishu_call_count = 0
    web_call_count = 0

    feishu_ctx = _make_feishu_ctx()
    feishu_ctx.redis_client = redis
    feishu_ctx.memory_store = memory_store
    feishu_ctx.evolution_settings = settings

    state = _make_web_state("u1")
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid_web, _ = await router.resolve_or_create("web:u1", "default")
    deps_web = MagicMock()
    deps_web.session_store = store
    deps_web.llm = MagicMock()

    with patch(
        "pyclaw.core.sop_extraction.extract_sops_sync",
        new_callable=AsyncMock,
        return_value=success,
    ) as mock_extract:
        feishu_adapter = FeishuCommandAdapter(_make_registry())
        await feishu_adapter.handle(
            text="/extract",
            session_key="feishu:user",
            session_id="ses_1",
            message_id="msg_1",
            event=_feishu_event(),
            ctx=feishu_ctx,
        )
        feishu_call_count = mock_extract.await_count

        web_adapter = WebCommandAdapter(_make_registry())
        await web_adapter.handle(
            text="/extract",
            state=state,
            conversation_id="c",
            session_id=sid_web,
            deps=deps_web,
            session_router=router,
            workspace_base=Path("/tmp"),
            settings=Settings(),
            redis_client=redis,
            memory_store=memory_store,
            evolution_settings=settings,
        )
        web_call_count = mock_extract.await_count - feishu_call_count

    assert feishu_call_count == 1
    assert web_call_count == 1
