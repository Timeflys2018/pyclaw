from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.commands.builtin import (
    cmd_extract,
    cmd_help,
    cmd_history,
    cmd_idle,
    cmd_new,
    cmd_reset,
    cmd_status,
    cmd_whoami,
    register_builtin_commands,
)
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.sop_extraction import ExtractionResult
from pyclaw.storage.session.base import InMemorySessionStore


def _build_ctx(
    channel: str = "feishu",
    *,
    session_id: str = "sid",
    session_key: str = "key",
    deps: MagicMock | None = None,
    session_router: SessionRouter | None = None,
    raw: dict | None = None,
    redis_client=None,
    memory_store=None,
    evolution_settings=None,
    nudge_hook=None,
    user_id: str = "user",
    workspace_id: str = "ws",
    registry: CommandRegistry | None = None,
) -> tuple[CommandContext, AsyncMock, AsyncMock]:
    reply = AsyncMock(return_value=None)
    dispatch = AsyncMock(return_value=None)
    return (
        CommandContext(
            session_id=session_id,
            session_key=session_key,
            workspace_id=workspace_id,
            user_id=user_id,
            channel=channel,
            deps=deps or MagicMock(),
            session_router=session_router or MagicMock(),
            workspace_base=Path("/tmp"),
            redis_client=redis_client,
            memory_store=memory_store,
            evolution_settings=evolution_settings,
            nudge_hook=nudge_hook,
            abort_event=asyncio.Event(),
            reply=reply,
            dispatch_user_message=dispatch,
            registry=registry,
            raw=raw or {"channel": channel},
        ),
        reply,
        dispatch,
    )


def _mock_deps_with_store(store: InMemorySessionStore) -> MagicMock:
    deps = MagicMock()
    deps.session_store = store
    deps.llm = MagicMock()
    deps.llm.default_model = "test-model"
    return deps


@pytest.mark.asyncio
async def test_cmd_new_rotates_and_no_followup_when_args_empty() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid, _ = await router.resolve_or_create("key1", "ws")
    deps = _mock_deps_with_store(store)
    ctx, reply, dispatch = _build_ctx(
        session_id=sid, session_key="key1", deps=deps, session_router=router
    )

    await cmd_new("", ctx)

    reply.assert_awaited_once()
    assert "新会话" in reply.await_args[0][0]
    dispatch.assert_not_awaited()
    new_sid = await store.get_current_session_id("key1")
    assert new_sid != sid


@pytest.mark.asyncio
async def test_cmd_new_dispatches_followup_when_args_present() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    await router.resolve_or_create("key1", "ws")
    deps = _mock_deps_with_store(store)
    ctx, reply, dispatch = _build_ctx(
        session_key="key1", deps=deps, session_router=router
    )

    await cmd_new("帮我做 X", ctx)

    reply.assert_awaited_once()
    dispatch.assert_awaited_once_with("帮我做 X")


@pytest.mark.asyncio
async def test_cmd_reset_uses_reset_message() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    await router.resolve_or_create("key1", "ws")
    deps = _mock_deps_with_store(store)
    ctx, reply, _ = _build_ctx(
        session_key="key1", deps=deps, session_router=router
    )

    await cmd_reset("", ctx)

    msg = reply.await_args[0][0]
    assert "重置" in msg


@pytest.mark.asyncio
async def test_cmd_status_format_includes_session_key() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid, _ = await router.resolve_or_create("feishu:cli:user", "ws")
    deps = _mock_deps_with_store(store)
    ctx, reply, _ = _build_ctx(
        session_id=sid,
        session_key="feishu:cli:user",
        deps=deps,
        session_router=router,
    )

    await cmd_status("", ctx)

    msg = reply.await_args[0][0]
    assert "feishu:cli:user" in msg
    assert "test-model" in msg


@pytest.mark.asyncio
async def test_cmd_whoami_feishu_uses_event() -> None:
    event = MagicMock()
    event.event.sender.sender_id.open_id = "ou_xyz"
    event.event.message.chat_type = "p2p"
    event.event.message.chat_id = ""
    ctx, reply, _ = _build_ctx(
        channel="feishu", raw={"channel": "feishu", "feishu_event": event}
    )

    await cmd_whoami("", ctx)

    msg = reply.await_args[0][0]
    assert "ou_xyz" in msg
    assert "p2p" in msg


@pytest.mark.asyncio
async def test_cmd_whoami_feishu_group_includes_chat_id() -> None:
    event = MagicMock()
    event.event.sender.sender_id.open_id = "ou_xyz"
    event.event.message.chat_type = "group"
    event.event.message.chat_id = "oc_chat1"
    ctx, reply, _ = _build_ctx(
        channel="feishu", raw={"channel": "feishu", "feishu_event": event}
    )

    await cmd_whoami("", ctx)

    msg = reply.await_args[0][0]
    assert "ou_xyz" in msg
    assert "group" in msg
    assert "oc_chat1" in msg


@pytest.mark.asyncio
async def test_cmd_whoami_web_uses_user_id() -> None:
    ctx, reply, _ = _build_ctx(channel="web", user_id="bob")

    await cmd_whoami("", ctx)

    msg = reply.await_args[0][0]
    assert "bob" in msg
    assert "web" in msg


@pytest.mark.asyncio
async def test_cmd_whoami_web_does_not_access_feishu_event() -> None:
    ctx, _, _ = _build_ctx(
        channel="web", user_id="bob", raw={"channel": "web"}
    )
    await cmd_whoami("", ctx)


@pytest.mark.asyncio
async def test_cmd_whoami_handler_in_web_accessing_feishu_event_raises_keyerror() -> None:
    ctx, _, _ = _build_ctx(channel="web", raw={"channel": "web"})
    with pytest.raises(KeyError):
        _ = ctx.raw["feishu_event"]


@pytest.mark.asyncio
async def test_cmd_history_empty() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    await router.resolve_or_create("k", "ws")
    deps = _mock_deps_with_store(store)
    ctx, reply, _ = _build_ctx(session_key="k", deps=deps, session_router=router)
    await cmd_history("", ctx)
    msg = reply.await_args[0][0]
    assert "只有一个会话" in msg or "历史会话" in msg


@pytest.mark.asyncio
async def test_cmd_history_with_entries() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    await router.resolve_or_create("k", "ws")
    await router.rotate("k", "ws")
    deps = _mock_deps_with_store(store)
    ctx, reply, _ = _build_ctx(session_key="k", deps=deps, session_router=router)
    await cmd_history("", ctx)
    msg = reply.await_args[0][0]
    assert "历史会话" in msg


@pytest.mark.asyncio
async def test_cmd_idle_30m() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid, _ = await router.resolve_or_create("k", "ws")
    deps = _mock_deps_with_store(store)
    ctx, reply, _ = _build_ctx(
        session_id=sid, session_key="k", deps=deps, session_router=router
    )
    await cmd_idle("30m", ctx)
    tree = await store.load(sid)
    assert tree is not None
    assert tree.header.idle_minutes_override == 30


@pytest.mark.asyncio
async def test_cmd_idle_off() -> None:
    store = InMemorySessionStore()
    router = SessionRouter(store=store)
    sid, _ = await router.resolve_or_create("k", "ws")
    deps = _mock_deps_with_store(store)
    ctx, reply, _ = _build_ctx(
        session_id=sid, session_key="k", deps=deps, session_router=router
    )
    await cmd_idle("off", ctx)
    msg = reply.await_args[0][0]
    assert "关闭" in msg


@pytest.mark.asyncio
async def test_cmd_idle_invalid() -> None:
    ctx, reply, _ = _build_ctx()
    await cmd_idle("garbage", ctx)
    msg = reply.await_args[0][0]
    assert "解析" in msg or "无法" in msg


@pytest.mark.asyncio
async def test_cmd_extract_success() -> None:
    deps = MagicMock()
    deps.session_store = InMemorySessionStore()
    deps.llm = MagicMock()
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    success = ExtractionResult(spawned=True, llm_returned_count=2, written=2)
    ctx, reply, _ = _build_ctx(
        deps=deps,
        redis_client=redis,
        memory_store=MagicMock(),
        evolution_settings=MagicMock(enabled=True),
    )
    with patch(
        "pyclaw.core.sop_extraction.extract_sops_sync",
        new_callable=AsyncMock,
        return_value=success,
    ):
        await cmd_extract("", ctx)
    reply.assert_awaited_once()
    assert "学到 2 条" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_cmd_extract_disabled() -> None:
    ctx, reply, _ = _build_ctx(
        deps=MagicMock(),
        redis_client=None,
        memory_store=None,
        evolution_settings=None,
    )
    await cmd_extract("", ctx)
    msg = reply.await_args[0][0]
    assert "未启用" in msg


@pytest.mark.asyncio
async def test_cmd_extract_rate_limited() -> None:
    deps = MagicMock()
    deps.session_store = InMemorySessionStore()
    deps.llm = MagicMock()
    redis = MagicMock()
    redis.set = AsyncMock(return_value=None)
    ctx, reply, _ = _build_ctx(
        deps=deps,
        redis_client=redis,
        memory_store=MagicMock(),
        evolution_settings=MagicMock(enabled=True),
    )
    await cmd_extract("", ctx)
    msg = reply.await_args[0][0]
    assert "频繁" in msg


@pytest.mark.asyncio
async def test_cmd_help_contains_d14_format() -> None:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    ctx, reply, _ = _build_ctx(registry=registry)
    await cmd_help("", ctx)
    msg = reply.await_args[0][0]
    assert "📖 PyClaw 命令帮助" in msg
    assert "📂 session" in msg
    assert "📂 inspection" in msg
    assert "📂 evolution" in msg
    assert "📂 config" in msg
    assert "/new" in msg
    assert "/extract" in msg
    assert "(别名: /learn)" in msg
    assert "<时长>" in msg


@pytest.mark.asyncio
async def test_register_builtin_includes_all_nine() -> None:
    registry = CommandRegistry()
    register_builtin_commands(registry)
    primary = sorted(s.name for s in registry.list_all())
    assert primary == [
        "/extract",
        "/help",
        "/history",
        "/idle",
        "/new",
        "/reset",
        "/status",
        "/whoami",
    ]
    assert registry.get("/learn") is registry.get("/extract")
