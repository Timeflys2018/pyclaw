from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyclaw.channels.feishu.dispatch import dispatch_message
from pyclaw.channels.base import InboundMessage
from pyclaw.channels.feishu.dedup import FeishuDedup
from pyclaw.channels.feishu.multimodal import feishu_image_to_block
from pyclaw.channels.feishu.queue import FeishuQueueRegistry
from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.infra.settings import FeishuSettings, Settings
from pyclaw.models import AgentEvent, Done, ErrorEvent, ImageBlock, TextChunk, ToolCallStart
from pyclaw.storage.workspace.base import WorkspaceStore

if TYPE_CHECKING:
    from pyclaw.channels.feishu.client import FeishuClient
    from pyclaw.channels.feishu.streaming import FeishuStreamingCard

logger = logging.getLogger(__name__)

_REACTION_DEDUP_WINDOW_S = 5.0
_reaction_last_handled: dict[str, float] = {}


def _reaction_should_handle(message_id: str) -> bool:
    now = time.time()
    last = _reaction_last_handled.get(message_id)
    if last is not None and (now - last) < _REACTION_DEDUP_WINDOW_S:
        return False
    _reaction_last_handled[message_id] = now
    if len(_reaction_last_handled) > 1000:
        cutoff = now - _REACTION_DEDUP_WINDOW_S * 6
        _reaction_last_handled.clear()
        _reaction_last_handled[message_id] = now
        _ = cutoff
    return True


async def handle_feishu_reaction_created(event: Any, ctx: FeishuContext) -> None:
    try:
        if not event.event:
            return
        data = event.event
        message_id: str = getattr(data, "message_id", "") or ""
        if not message_id:
            return
        if not ctx.feishu_client.is_bot_message(message_id):
            return
        if not _reaction_should_handle(message_id):
            return
        emoji_type: str = ""
        if getattr(data, "reaction_type", None) is not None:
            emoji_type = getattr(data.reaction_type, "emoji_type", "") or ""
        if not emoji_type:
            return
        ok = await ctx.feishu_client.create_reaction(message_id, emoji_type)
        if not ok:
            logger.info(
                "reaction mirror skipped (API failed) msg=%s emoji=%s",
                message_id, emoji_type,
            )
    except Exception:
        logger.exception("handle_feishu_reaction_created failed")


@dataclass
class FeishuContext:
    settings: FeishuSettings
    settings_full: Settings
    feishu_client: FeishuClient
    deps: AgentRunnerDeps
    dedup: FeishuDedup
    workspace_store: WorkspaceStore
    bot_open_id: str
    session_router: SessionRouter = field(default_factory=lambda: SessionRouter(store=None))  # type: ignore[arg-type]
    workspace_base: Path = field(default_factory=lambda: Path.home() / ".pyclaw/workspaces")
    bootstrap_files: list[str] = field(default_factory=lambda: ["AGENTS.md"])
    queue_registry: FeishuQueueRegistry | None = None
    # Self-evolution deps (optional)
    redis_client: Any = None
    memory_store: Any = None
    evolution_settings: Any = None
    nudge_hook: Any = None
    agent_settings: Any = None
    admin_user_ids: list[str] = field(default_factory=list)


def build_session_key(app_id: str, event: Any, scope: str) -> str:
    msg = event.event.message
    sender = event.event.sender
    chat_type: str = msg.chat_type or ""
    chat_id: str = msg.chat_id or ""
    open_id: str = (sender.sender_id.open_id or "") if sender and sender.sender_id else ""
    thread_id: str = msg.thread_id or ""

    if chat_type == "p2p":
        return f"feishu:{app_id}:{open_id}"
    if scope == "user":
        return f"feishu:{app_id}:{chat_id}:{open_id}"
    if scope == "thread" and thread_id:
        return f"feishu:{app_id}:{chat_id}:thread:{thread_id}"
    return f"feishu:{app_id}:{chat_id}"


build_session_id = build_session_key


def is_bot_mentioned(event: Any, bot_open_id: str) -> bool:
    mentions = (event.event.message.mentions or []) if event.event and event.event.message else []
    return any(
        m.id and m.id.open_id == bot_open_id
        for m in mentions
    )


def extract_text_and_images_from_event(event: Any) -> tuple[str | None, list[str]]:
    if not event.event or not event.event.message:
        return None, []
    msg = event.event.message
    msg_type = msg.message_type or ""
    content_str = msg.content or ""
    logger.debug(
        "extract_text_and_images: msg_type=%s raw_content=%r",
        msg_type, content_str[:2000],
    )

    if msg_type == "text":
        try:
            data = json.loads(content_str)
            return str(data.get("text", "")), []
        except Exception:
            return content_str, []

    if msg_type == "image":
        try:
            data = json.loads(content_str)
            image_key = str(data.get("image_key") or "")
            return None, [image_key] if image_key else []
        except Exception:
            return None, []

    if msg_type == "media":
        try:
            data = json.loads(content_str)
            image_key = str(data.get("image_key") or "")
            return None, [image_key] if image_key else []
        except Exception:
            return None, []

    if msg_type == "post":
        try:
            data = json.loads(content_str)
            text_parts: list[str] = []
            image_keys: list[str] = []

            content_grids: list[Any] = []
            top_level = data.get("content")
            if isinstance(top_level, list):
                content_grids.append(top_level)
            for value in data.values():
                if isinstance(value, dict):
                    inner = value.get("content")
                    if isinstance(inner, list):
                        content_grids.append(inner)

            for grid in content_grids:
                for row in grid:
                    if not isinstance(row, list):
                        continue
                    for span in row:
                        if not isinstance(span, dict):
                            continue
                        tag = span.get("tag")
                        if tag == "text":
                            text_parts.append(str(span.get("text", "")))
                        elif tag == "a":
                            text_parts.append(str(span.get("text", "")))
                        elif tag == "at":
                            user_name = span.get("user_name")
                            if user_name:
                                text_parts.append(f"@{user_name}")
                        elif tag == "img":
                            key = span.get("image_key")
                            if key:
                                image_keys.append(str(key))
                        elif tag == "media":
                            key = span.get("image_key")
                            if key:
                                image_keys.append(str(key))
            text = " ".join(p for p in text_parts if p) if text_parts else None
            return text, image_keys
        except Exception:
            return None, []

    return None, []


def extract_text_from_event(event: Any) -> str | None:
    text, _ = extract_text_and_images_from_event(event)
    return text


async def build_group_context(client: FeishuClient, chat_id: str, size: int) -> str:
    msgs = await client.get_recent_messages(chat_id, limit=size)
    lines = []
    for m in reversed(msgs):
        sender = m.get("sender_id", "unknown")
        content = m.get("content", "")
        lines.append(f"[{sender}]: {content}")
    return "\n".join(lines)


async def handle_stop_feishu(ctx: "FeishuContext", session_id: str, message_id: str) -> None:
    assert ctx.queue_registry is not None, "queue_registry must be set on FeishuContext"
    rc = ctx.queue_registry.get_run_control(session_id)
    if rc.is_active():
        rc.chat_done_handled_externally = True
        rc.stop()
        await ctx.feishu_client.reply_text(message_id, "🛑 已停止")
    else:
        await ctx.feishu_client.reply_text(message_id, "⚠️ 没有正在运行的任务")


async def handle_feishu_message(event: Any, ctx: FeishuContext) -> None:
    if not event.event or not event.event.message:
        return

    msg = event.event.message
    message_id: str = msg.message_id or ""
    chat_type: str = msg.chat_type or ""
    chat_id: str = msg.chat_id or ""
    msg_type: str = msg.message_type or ""

    if await ctx.dedup.is_duplicate(message_id):
        logger.debug("duplicate message %s, skipping", message_id)
        return

    if chat_type == "group" and not is_bot_mentioned(event, ctx.bot_open_id):
        logger.debug("group message without bot mention, skipping")
        return

    text, image_keys = extract_text_and_images_from_event(event)

    if len(image_keys) > 5:
        logger.warning(
            "feishu message %s has %d images, truncating to 5",
            message_id, len(image_keys),
        )
        image_keys = image_keys[:5]

    attachments: list[ImageBlock] = []
    for image_key in image_keys:
        try:
            block = await feishu_image_to_block(ctx.feishu_client, message_id, image_key)
            attachments.append(block)
        except Exception:
            logger.exception(
                "failed to download image %s for message %s",
                image_key, message_id,
            )

    if text is None and not attachments:
        logger.debug("unsupported message type %s, skipping", msg_type)
        return

    session_key = build_session_key(ctx.settings.app_id, event, ctx.settings.session_scope)
    workspace_id = session_key.replace(":", "_")
    workspace_path = ctx.workspace_base / workspace_id

    session_id, _ = await ctx.session_router.resolve_or_create(session_key, workspace_id)

    idle_minutes = (
        ctx.settings.idle_minutes
    )
    _tree = await ctx.session_router.store.load(session_id)
    if _tree and _tree.header.idle_minutes_override is not None:
        idle_minutes = _tree.header.idle_minutes_override

    if await ctx.session_router.check_idle_reset(session_key, session_id, idle_minutes):
        logger.info("idle reset triggered for session %s", session_id)
        session_id, _ = await ctx.session_router.rotate(session_key, workspace_id)

    if text is not None and text.strip().lower() == "/stop":
        await handle_stop_feishu(ctx, session_id, message_id)
        return

    if text is not None and text.startswith("/"):
        from pyclaw.channels.feishu.command_adapter import FeishuCommandAdapter
        adapter = FeishuCommandAdapter()
        handled = await adapter.handle(
            text=text,
            session_key=session_key,
            session_id=session_id,
            message_id=message_id,
            event=event,
            ctx=ctx,
        )
        if handled:
            return

    extra_system_parts: list[str] = []

    if chat_type == "group" and ctx.settings.group_context == "recent":
        group_ctx = await build_group_context(ctx.feishu_client, chat_id, ctx.settings.group_context_size)
        if group_ctx:
            extra_system_parts.append(
                f"## 群组最近 {ctx.settings.group_context_size} 条消息\n{group_ctx}"
            )

    extra_system = "\n\n".join(extra_system_parts)

    inbound = InboundMessage(
        session_id=session_id,
        user_message=text or "",
        workspace_id=workspace_id,
        channel="feishu",
        attachments=attachments,
    )

    async def _fallback_reply(reply_text: str) -> None:
        await ctx.feishu_client.reply_text(message_id, reply_text)

    async def _run() -> None:
        await _dispatch_and_reply(inbound, ctx, message_id, workspace_path, extra_system)
        await ctx.session_router.update_last_interaction(session_id)

    assert ctx.queue_registry is not None, "queue_registry must be set on FeishuContext"
    await ctx.queue_registry.enqueue(session_id, _run(), owner=session_key)


async def _dispatch_and_reply(
    inbound: InboundMessage,
    ctx: FeishuContext,
    message_id: str,
    workspace_path: Path,
    extra_system: str,
) -> None:
    from pyclaw.channels.feishu.streaming import FeishuStreamingCard, StreamingConfig

    async def _fallback(reply_text: str) -> None:
        await ctx.feishu_client.reply_text(message_id, reply_text)

    sc = ctx.settings.streaming
    streaming_config = StreamingConfig(
        print_frequency_ms=sc.print_frequency_ms,
        print_step=sc.print_step,
        print_strategy=sc.print_strategy,
        summary=sc.summary,
        throttle_ms=sc.throttle_ms,
    )
    card = FeishuStreamingCard(
        ctx.feishu_client._client,
        message_id,
        streaming_config,
        track_bot_message=ctx.feishu_client._track_bot_message,
    )
    try:
        await card.start()
        use_card = True
    except Exception:
        logger.exception("Failed to start streaming card, falling back to text")
        use_card = False

    if use_card:
        await stream_agent_reply(
            dispatch_message(
                inbound, ctx.deps, workspace_path=workspace_path, extra_system=extra_system,
                queue_registry=ctx.queue_registry,
            ),
            card=card,
            fallback_fn=_fallback,
        )
    else:
        final_text = ""
        async for ev in dispatch_message(
            inbound, ctx.deps, workspace_path=workspace_path, extra_system=extra_system,
            queue_registry=ctx.queue_registry,
        ):
            if isinstance(ev, Done):
                final_text = ev.final_message
        await _fallback(final_text or "(no response)")


async def stream_agent_reply(
    events: AsyncIterator[AgentEvent],
    card: FeishuStreamingCard,
    fallback_fn: Any,
) -> None:
    accumulated = ""
    try:
        async for event in events:
            if isinstance(event, TextChunk):
                accumulated += event.text
                await card.update(accumulated)
            elif isinstance(event, ToolCallStart):
                accumulated += f"\n🔧 {event.name}...\n"
                await card.update(accumulated)
            elif isinstance(event, Done):
                await card.finish(event.final_message)
                return
            elif isinstance(event, ErrorEvent):
                await card.error(event.message)
                return
        if accumulated:
            await card.finish(accumulated)
    except Exception:
        logger.exception("stream_agent_reply failed")
        try:
            await fallback_fn(accumulated or "(error)")
        except Exception:
            logger.exception("fallback_fn also failed")
