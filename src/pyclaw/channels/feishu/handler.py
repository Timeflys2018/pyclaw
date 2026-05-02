from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyclaw.channels.feishu.dispatch import dispatch_message
from pyclaw.channels.base import InboundMessage
from pyclaw.channels.feishu.dedup import FeishuDedup
from pyclaw.channels.feishu.multimodal import feishu_image_to_block
from pyclaw.channels.feishu.queue import enqueue
from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.infra.settings import FeishuSettings
from pyclaw.models import AgentEvent, Done, ErrorEvent, ImageBlock, TextChunk, ToolCallStart
from pyclaw.storage.workspace.base import WorkspaceStore

if TYPE_CHECKING:
    from pyclaw.channels.feishu.client import FeishuClient
    from pyclaw.channels.feishu.streaming import FeishuStreamingCard

logger = logging.getLogger(__name__)


@dataclass
class FeishuContext:
    settings: FeishuSettings
    feishu_client: FeishuClient
    deps: AgentRunnerDeps
    dedup: FeishuDedup
    workspace_store: WorkspaceStore
    bot_open_id: str
    session_router: SessionRouter = field(default_factory=lambda: SessionRouter(store=None))  # type: ignore[arg-type]
    workspace_base: Path = field(default_factory=lambda: Path.home() / ".pyclaw/workspaces")
    bootstrap_files: list[str] = field(default_factory=lambda: ["AGENTS.md"])


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


def extract_text_from_event(event: Any) -> str | None:
    if not event.event or not event.event.message:
        return None
    msg = event.event.message
    msg_type = msg.message_type or ""
    content_str = msg.content or ""

    if msg_type == "text":
        try:
            data = json.loads(content_str)
            return str(data.get("text", ""))
        except Exception:
            return content_str

    if msg_type == "post":
        try:
            data = json.loads(content_str)
            parts: list[str] = []
            for lang_content in data.values():
                if isinstance(lang_content, dict):
                    for row in lang_content.get("content", []):
                        for span in row:
                            if isinstance(span, dict) and span.get("tag") == "text":
                                parts.append(str(span.get("text", "")))
            if parts:
                return " ".join(parts)
            content_body = data.get("content") or data.get("zh_cn", {}).get("content", [])
            for row in content_body:
                for span in row:
                    if isinstance(span, dict) and span.get("tag") == "text":
                        parts.append(str(span.get("text", "")))
            return " ".join(parts) if parts else None
        except Exception:
            return None

    return None


async def build_group_context(client: FeishuClient, chat_id: str, size: int) -> str:
    msgs = await client.get_recent_messages(chat_id, limit=size)
    lines = []
    for m in reversed(msgs):
        sender = m.get("sender_id", "unknown")
        content = m.get("content", "")
        lines.append(f"[{sender}]: {content}")
    return "\n".join(lines)


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

    text = extract_text_from_event(event)

    attachments: list[ImageBlock] = []
    if msg_type == "image":
        try:
            content_data = json.loads(msg.content or "{}")
            image_key = content_data.get("image_key", "")
            if image_key:
                block = await feishu_image_to_block(ctx.feishu_client, message_id, image_key)
                attachments.append(block)
                if text is None:
                    text = ""
        except Exception:
            logger.exception("failed to download image for message %s", message_id)

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

    if text is not None and text.startswith("/"):
        from pyclaw.channels.feishu.commands import handle_command
        handled = await handle_command(
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
    if ctx.deps.workspace_store is None:
        from pyclaw.core.context.bootstrap import load_bootstrap_context
        bootstrap_ctx = await load_bootstrap_context(
            workspace_id, ctx.workspace_store, ctx.bootstrap_files
        )
        if bootstrap_ctx:
            extra_system_parts.append(bootstrap_ctx)

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

    await enqueue(session_id, _run())


async def _dispatch_and_reply(
    inbound: InboundMessage,
    ctx: FeishuContext,
    message_id: str,
    workspace_path: Path,
    extra_system: str,
) -> None:
    from pyclaw.channels.feishu.streaming import FeishuStreamingCard

    async def _fallback(reply_text: str) -> None:
        await ctx.feishu_client.reply_text(message_id, reply_text)

    card = FeishuStreamingCard(ctx.feishu_client._client, message_id)
    try:
        await card.start()
        use_card = True
    except Exception:
        logger.exception("Failed to start streaming card, falling back to text")
        use_card = False

    if use_card:
        await stream_agent_reply(
            dispatch_message(inbound, ctx.deps, workspace_path=workspace_path, extra_system=extra_system),
            card=card,
            fallback_fn=_fallback,
        )
    else:
        final_text = ""
        async for ev in dispatch_message(inbound, ctx.deps, workspace_path=workspace_path, extra_system=extra_system):
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
