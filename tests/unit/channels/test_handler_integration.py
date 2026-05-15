from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.channels.feishu.dedup import FeishuDedup
from pyclaw.channels.feishu.handler import FeishuContext, handle_feishu_message
from pyclaw.channels.feishu.queue import FeishuQueueRegistry
from pyclaw.channels.session_router import SessionRouter
from pyclaw.infra.settings import FeishuSettings, Settings
from pyclaw.infra.task_manager import TaskManager
from pyclaw.models import Done
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
    from pyclaw.core.commands.builtin import register_builtin_commands
    from pyclaw.core.commands.registry import (
        get_default_registry,
        reset_default_registry,
    )
    from pyclaw.core.hooks import HookRegistry
    from pyclaw.models import AgentRunConfig

    reset_default_registry()
    register_builtin_commands(get_default_registry())

    class _OneShotLLM(LLMClient):
        def __init__(self) -> None:
            super().__init__(default_model="fake")

        async def stream(
            self,
            *,
            messages,
            model=None,
            tools=None,
            system=None,
            idle_seconds=0.0,
            abort_event=None,
        ):
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

    tm = TaskManager()
    queue_registry = FeishuQueueRegistry(task_manager=tm)
    return FeishuContext(
        settings=settings,
        settings_full=Settings(),
        feishu_client=feishu_client,
        deps=deps,
        dedup=dedup,
        workspace_store=workspace_store,
        bot_open_id="bot_open_id",
        session_router=router,
        workspace_base=tmp_path / "workspaces",
        queue_registry=queue_registry,
    )


@pytest.mark.asyncio
async def test_handle_feishu_message_creates_session_and_enqueues(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    event = _make_text_event("你好", message_id="msg_001")

    enqueued: list[str] = []
    _original = ctx.queue_registry.enqueue

    async def _capture_enqueue(session_id: str, coro, **_kw) -> None:
        enqueued.append(session_id)
        await coro

    with patch.object(ctx.queue_registry, "enqueue", side_effect=_capture_enqueue):
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

    enqueued: list[str] = []

    async def _capture_enqueue(session_id: str, coro, **_kw) -> None:
        enqueued.append(session_id)
        await coro

    with patch.object(ctx.queue_registry, "enqueue", side_effect=_capture_enqueue):
        await handle_feishu_message(event, ctx)
        await handle_feishu_message(event, ctx)

    assert len(enqueued) == 1


@pytest.mark.asyncio
async def test_handle_feishu_message_group_no_mention_skipped(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    event = _make_text_event("大家好", chat_type="group", message_id="msg_group_001")

    enqueued: list[str] = []

    async def _capture_enqueue(session_id: str, coro, **_kw) -> None:
        enqueued.append(session_id)
        await coro

    with patch.object(ctx.queue_registry, "enqueue", side_effect=_capture_enqueue):
        await handle_feishu_message(event, ctx)

    assert len(enqueued) == 0


@pytest.mark.asyncio
async def test_handle_feishu_message_slash_command_intercepted(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    sid, _ = await ctx.session_router.resolve_or_create(
        "feishu:cli_x:ou_test", "feishu_cli_x_ou_test"
    )
    event = _make_text_event("/help", message_id="msg_help_001")

    enqueued = []

    async def _capture_enqueue(session_id: str, coro, **_kw) -> None:
        enqueued.append(session_id)
        await coro

    with patch.object(ctx.queue_registry, "enqueue", side_effect=_capture_enqueue):
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

    async def _run_immediately(session_id: str, coro, **_kw) -> None:
        await coro

    with patch.object(ctx.queue_registry, "enqueue", side_effect=_run_immediately):
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
        yield Done(final_message="fallback response", usage={})

    with patch("pyclaw.channels.feishu.handler.dispatch_message", side_effect=_mock_stream):
        with patch("pyclaw.channels.feishu.streaming.FeishuStreamingCard") as mock_card_cls:
            mock_card = MagicMock()
            mock_card.start = AsyncMock(side_effect=RuntimeError("card unavailable"))
            mock_card_cls.return_value = mock_card

            await _dispatch_and_reply(
                inbound, ctx, "msg_fallback_001", tmp_path / "ws", extra_system=""
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

    async def _capture_enqueue(session_id: str, coro, **_kw) -> None:
        all_enqueued.append((session_id, coro))
        coro.close()

    with patch.object(ctx.queue_registry, "enqueue", side_effect=_capture_enqueue):
        await handle_feishu_message(event, ctx)

    new_sid = await store.get_current_session_id("feishu:cli_x:ou_test")
    assert new_sid is not None
    assert new_sid != old_sid

    ctx.feishu_client.reply_text.assert_awaited_once()
    reply = ctx.feishu_client.reply_text.call_args[0][1]
    assert "新会话" in reply

    followup_enqueued = [s for s, _ in all_enqueued if s == new_sid]
    assert len(followup_enqueued) >= 1


def _make_ctx_with_workspace_store(store: InMemorySessionStore, tmp_path: Path) -> FeishuContext:
    from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
    from pyclaw.core.agent.runner import AgentRunnerDeps
    from pyclaw.core.agent.tools.registry import ToolRegistry
    from pyclaw.core.hooks import HookRegistry
    from pyclaw.models import AgentRunConfig

    class _OneShotLLM(LLMClient):
        def __init__(self) -> None:
            super().__init__(default_model="fake")

        async def stream(
            self,
            *,
            messages,
            model=None,
            tools=None,
            system=None,
            idle_seconds=0.0,
            abort_event=None,
        ):
            yield LLMStreamChunk(text_delta="hi")
            yield LLMStreamChunk(finish_reason="stop", usage=LLMUsage())

    ws_store = FileWorkspaceStore(base_dir=tmp_path / "workspaces")
    deps = AgentRunnerDeps(
        llm=_OneShotLLM(),
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        session_store=store,
        config=AgentRunConfig(),
        workspace_store=ws_store,
    )

    dedup = FeishuDedup()
    router = SessionRouter(store=store)
    tm = TaskManager()
    queue_registry = FeishuQueueRegistry(task_manager=tm)

    return FeishuContext(
        settings=FeishuSettings(enabled=True, app_id="cli_x", app_secret="s"),
        settings_full=Settings(),
        feishu_client=MagicMock(reply_text=AsyncMock(return_value=None), _client=MagicMock()),
        deps=deps,
        dedup=dedup,
        workspace_store=ws_store,
        bot_open_id="bot_open_id",
        session_router=router,
        workspace_base=tmp_path / "workspaces",
        queue_registry=queue_registry,
    )


@pytest.mark.asyncio
async def test_channel_skips_bootstrap_when_deps_has_workspace_store(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx_with_workspace_store(store, tmp_path)
    ws_id = "feishu_cli_x_ou_test"
    (tmp_path / "workspaces" / ws_id).mkdir(parents=True)
    (tmp_path / "workspaces" / ws_id / "AGENTS.md").write_text("engine handles this")

    event = _make_text_event("hello", message_id="msg_guard_001")

    extra_systems_seen: list[str] = []
    original_dispatch = None

    async def _capture_dispatch_and_reply(inbound, ctx, message_id, workspace_path, extra_system):
        extra_systems_seen.append(extra_system)

    async def _run_immediately(session_id: str, coro, **_kw) -> None:
        await coro

    with patch.object(ctx.queue_registry, "enqueue", side_effect=_run_immediately):
        with patch(
            "pyclaw.channels.feishu.handler._dispatch_and_reply",
            side_effect=_capture_dispatch_and_reply,
        ):
            await handle_feishu_message(event, ctx)

    assert len(extra_systems_seen) == 1
    assert extra_systems_seen[0] == ""


@pytest.mark.asyncio
async def test_channel_no_longer_passes_bootstrap_via_extra_system(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    ws_id = "feishu_cli_x_ou_test"
    (tmp_path / "workspaces" / ws_id).mkdir(parents=True)
    (tmp_path / "workspaces" / ws_id / "AGENTS.md").write_text("channel fallback content")

    event = _make_text_event("hello", message_id="msg_fallback_001")

    extra_systems_seen: list[str] = []

    async def _capture_dispatch_and_reply(inbound, ctx, message_id, workspace_path, extra_system):
        extra_systems_seen.append(extra_system)

    async def _run_immediately(session_id: str, coro, **_kw) -> None:
        await coro

    with patch.object(ctx.queue_registry, "enqueue", side_effect=_run_immediately):
        with patch(
            "pyclaw.channels.feishu.handler._dispatch_and_reply",
            side_effect=_capture_dispatch_and_reply,
        ):
            await handle_feishu_message(event, ctx)

    assert len(extra_systems_seen) == 1
    assert "channel fallback content" not in extra_systems_seen[0]


@pytest.mark.asyncio
async def test_workspace_base_comes_from_context_not_hardcoded(tmp_path: Path) -> None:
    store = InMemorySessionStore()
    custom_base = tmp_path / "custom_workspace"
    custom_base.mkdir()

    from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
    from pyclaw.core.agent.runner import AgentRunnerDeps
    from pyclaw.core.agent.tools.registry import ToolRegistry
    from pyclaw.core.hooks import HookRegistry
    from pyclaw.models import AgentRunConfig

    class _OneShotLLM(LLMClient):
        def __init__(self) -> None:
            super().__init__(default_model="fake")

        async def stream(
            self,
            *,
            messages,
            model=None,
            tools=None,
            system=None,
            idle_seconds=0.0,
            abort_event=None,
        ):
            yield LLMStreamChunk(text_delta="hi")
            yield LLMStreamChunk(finish_reason="stop", usage=LLMUsage())

    deps = AgentRunnerDeps(
        llm=_OneShotLLM(),
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        session_store=store,
        config=AgentRunConfig(),
    )
    dedup = FeishuDedup()
    ws_store = FileWorkspaceStore(base_dir=custom_base)
    router = SessionRouter(store=store)
    tm = TaskManager()
    qr = FeishuQueueRegistry(task_manager=tm)

    ctx = FeishuContext(
        settings=FeishuSettings(enabled=True, app_id="cli_x", app_secret="s"),
        settings_full=Settings(),
        feishu_client=MagicMock(reply_text=AsyncMock(return_value=None), _client=MagicMock()),
        deps=deps,
        dedup=dedup,
        workspace_store=ws_store,
        bot_open_id="bot",
        session_router=router,
        workspace_base=custom_base,
        queue_registry=qr,
    )

    event = _make_text_event("test", message_id="msg_base_001")

    workspace_paths_seen: list[Path] = []

    async def _capture(inbound, ctx, message_id, workspace_path, extra_system):
        workspace_paths_seen.append(workspace_path)

    async def _run_immediately(session_id: str, coro, **_kw) -> None:
        await coro

    with patch.object(ctx.queue_registry, "enqueue", side_effect=_run_immediately):
        with patch("pyclaw.channels.feishu.handler._dispatch_and_reply", side_effect=_capture):
            await handle_feishu_message(event, ctx)

    assert len(workspace_paths_seen) == 1
    assert workspace_paths_seen[0].parent == custom_base


def _make_post_event(
    content_json: str,
    chat_type: str = "p2p",
    open_id: str = "ou_test",
    chat_id: str = "oc_chat1",
    message_id: str = "msg_post",
) -> Any:
    event = MagicMock()
    event.event.sender.sender_id.open_id = open_id
    event.event.message.chat_type = chat_type
    event.event.message.chat_id = chat_id
    event.event.message.thread_id = ""
    event.event.message.message_type = "post"
    event.event.message.message_id = message_id
    event.event.message.content = content_json
    event.event.message.mentions = []
    return event


def _make_pure_image_event(
    image_key: str,
    chat_type: str = "p2p",
    open_id: str = "ou_test",
    chat_id: str = "oc_chat1",
    message_id: str = "msg_img",
) -> Any:
    event = MagicMock()
    event.event.sender.sender_id.open_id = open_id
    event.event.message.chat_type = chat_type
    event.event.message.chat_id = chat_id
    event.event.message.thread_id = ""
    event.event.message.message_type = "image"
    event.event.message.message_id = message_id
    event.event.message.content = '{"image_key": "' + image_key + '"}'
    event.event.message.mentions = []
    return event


@pytest.mark.asyncio
async def test_handle_post_with_one_image_attaches(tmp_path: Path) -> None:
    from pyclaw.models import ImageBlock as _ImageBlock

    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    content = (
        '{"zh_cn": {"content": [['
        '{"tag": "text", "text": "look"},'
        '{"tag": "img", "image_key": "img_xyz"}'
        "]]}}"
    )
    event = _make_post_event(content, message_id="msg_post_one_img")

    async def fake_image_to_block(client, mid, key):
        return _ImageBlock(type="image", data="b64", mime_type="image/png")

    captured: dict[str, Any] = {}

    async def fake_dispatch(inbound, ctx_, message_id, workspace_path, extra_system):
        captured["inbound"] = inbound

    async def _run_immediately(session_id, coro, **_kw):
        await coro

    with patch(
        "pyclaw.channels.feishu.handler.feishu_image_to_block", side_effect=fake_image_to_block
    ):
        with patch.object(ctx.queue_registry, "enqueue", side_effect=_run_immediately):
            with patch(
                "pyclaw.channels.feishu.handler._dispatch_and_reply", side_effect=fake_dispatch
            ):
                await handle_feishu_message(event, ctx)

    inbound = captured["inbound"]
    assert len(inbound.attachments) == 1
    assert inbound.attachments[0].type == "image"
    assert "look" in inbound.user_message


@pytest.mark.asyncio
async def test_handle_seven_images_truncated_to_five_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    from pyclaw.models import ImageBlock as _ImageBlock

    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    spans = ",".join(f'{{"tag": "img", "image_key": "k{i}"}}' for i in range(7))
    content = '{"zh_cn": {"content": [[' + spans + "]]}}"
    event = _make_post_event(content, message_id="msg_seven_imgs")

    async def fake_image_to_block(client, mid, key):
        return _ImageBlock(type="image", data="b64", mime_type="image/png")

    captured: dict[str, Any] = {}

    async def fake_dispatch(inbound, ctx_, message_id, workspace_path, extra_system):
        captured["inbound"] = inbound

    async def _run_immediately(session_id, coro, **_kw):
        await coro

    import logging

    with caplog.at_level(logging.WARNING, logger="pyclaw.channels.feishu.handler"):
        with patch(
            "pyclaw.channels.feishu.handler.feishu_image_to_block", side_effect=fake_image_to_block
        ):
            with patch.object(ctx.queue_registry, "enqueue", side_effect=_run_immediately):
                with patch(
                    "pyclaw.channels.feishu.handler._dispatch_and_reply", side_effect=fake_dispatch
                ):
                    await handle_feishu_message(event, ctx)

    inbound = captured["inbound"]
    assert len(inbound.attachments) == 5
    assert any("truncating to 5" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_handle_pure_image_no_text_forces_empty_string(tmp_path: Path) -> None:
    from pyclaw.models import ImageBlock as _ImageBlock

    store = InMemorySessionStore()
    ctx = _make_ctx(store, tmp_path)
    event = _make_pure_image_event("img_only_key", message_id="msg_pure_img")

    async def fake_image_to_block(client, mid, key):
        return _ImageBlock(type="image", data="b64", mime_type="image/jpeg")

    captured: dict[str, Any] = {}

    async def fake_dispatch(inbound, ctx_, message_id, workspace_path, extra_system):
        captured["inbound"] = inbound

    async def _run_immediately(session_id, coro, **_kw):
        await coro

    with patch(
        "pyclaw.channels.feishu.handler.feishu_image_to_block", side_effect=fake_image_to_block
    ):
        with patch.object(ctx.queue_registry, "enqueue", side_effect=_run_immediately):
            with patch(
                "pyclaw.channels.feishu.handler._dispatch_and_reply", side_effect=fake_dispatch
            ):
                await handle_feishu_message(event, ctx)

    inbound = captured["inbound"]
    assert inbound.user_message == ""
    assert len(inbound.attachments) == 1
