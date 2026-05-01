from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyclaw.channels.feishu.handler import FeishuContext

logger = logging.getLogger(__name__)

_HELP_TEXT = """\
📋 **PyClaw 可用命令**

/new [消息]   — 开始新会话（可选：附带第一条消息）
/reset [消息] — 重置当前会话
/status       — 显示当前会话信息
/whoami       — 显示你的身份信息
/history      — 查看历史会话列表
/idle <时长>  — 设置空闲自动重置（如 30m、2h、off）
/help         — 显示此帮助
"""


def _parse_idle_duration(arg: str) -> int | None:
    arg = arg.strip().lower()
    if arg in ("off", "0", "disable", "关闭"):
        return 0
    m = re.fullmatch(r"(\d+)m(?:ins?|inutes?)?", arg)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d+)h(?:ours?)?", arg)
    if m:
        return int(m.group(1)) * 60
    return None


async def handle_command(
    text: str,
    session_key: str,
    session_id: str,
    message_id: str,
    event: Any,
    ctx: FeishuContext,
) -> bool:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return False

    lower = stripped.lower()

    for prefix in ("/new", "/reset", "/status", "/whoami", "/history", "/help", "/idle"):
        if lower == prefix or lower.startswith(prefix + " "):
            break
    else:
        return False

    workspace_id = session_key.replace(":", "_")

    if lower.startswith("/new") or lower.startswith("/reset"):
        is_new = lower.startswith("/new")
        args = stripped[4:].strip() if is_new else stripped[6:].strip()
        reply_text, followup = await _cmd_new_or_reset(
            args=args,
            session_key=session_key,
            workspace_id=workspace_id,
            is_new=is_new,
            ctx=ctx,
        )
        await ctx.feishu_client.reply_text(message_id, reply_text)
        if followup:
            from pyclaw.channels.base import InboundMessage
            from pyclaw.channels.feishu.queue import enqueue
            from pyclaw.channels.feishu.handler import _dispatch_and_reply

            new_sid = await ctx.session_router.store.get_current_session_id(session_key) or session_id
            workspace_path = ctx.workspace_base / workspace_id
            inbound = InboundMessage(
                session_id=new_sid,
                user_message=followup,
                workspace_id=workspace_id,
                channel="feishu",
            )

            from pyclaw.core.context.bootstrap import load_bootstrap_context
            followup_extra_system = await load_bootstrap_context(
                workspace_id, ctx.workspace_store, ctx.bootstrap_files
            )

            async def _run_followup() -> None:
                await _dispatch_and_reply(inbound, ctx, message_id, workspace_path, followup_extra_system)
                await ctx.session_router.update_last_interaction(new_sid)

            await enqueue(new_sid, _run_followup())
        return True

    if lower == "/status":
        reply = await _cmd_status(session_key, session_id, ctx)
        await ctx.feishu_client.reply_text(message_id, reply)
        return True

    if lower == "/whoami":
        reply = _cmd_whoami(event)
        await ctx.feishu_client.reply_text(message_id, reply)
        return True

    if lower == "/history":
        reply = await _cmd_history(session_key, ctx)
        await ctx.feishu_client.reply_text(message_id, reply)
        return True

    if lower == "/help":
        await ctx.feishu_client.reply_text(message_id, _HELP_TEXT)
        return True

    if lower.startswith("/idle"):
        args = stripped[5:].strip()
        reply = await _cmd_idle(args, session_id, ctx)
        await ctx.feishu_client.reply_text(message_id, reply)
        return True

    return False


async def _cmd_new_or_reset(
    args: str,
    session_key: str,
    workspace_id: str,
    is_new: bool,
    ctx: FeishuContext,
) -> tuple[str, str | None]:
    await ctx.session_router.rotate(session_key, workspace_id)
    verb = "新会话已开始" if is_new else "会话已重置"
    reply = f"✨ {verb}，之前的对话已归档。"
    followup = args if args else None
    return reply, followup


async def _cmd_status(session_key: str, session_id: str, ctx: FeishuContext) -> str:
    tree = await ctx.session_router.store.load(session_id)
    msg_count = len(tree.entries) if tree else 0
    created_at = tree.header.created_at if tree else "unknown"
    short_id = session_id.split(":")[-1] if ":" in session_id else session_id[-8:]
    model = ctx.deps.llm.default_model if hasattr(ctx.deps, "llm") else "unknown"
    lines = [
        "📊 **会话状态**",
        f"SessionKey: `{session_key}`",
        f"SessionId:  `...{short_id}`",
        f"消息数:     {msg_count}",
        f"模型:       {model}",
        f"创建时间:   {created_at[:19].replace('T', ' ')}",
    ]
    return "\n".join(lines)


def _cmd_whoami(event: Any) -> str:
    if not event.event or not event.event.sender or not event.event.message:
        return "❓ 无法获取身份信息"
    sender = event.event.sender
    msg = event.event.message
    open_id = (sender.sender_id.open_id or "unknown") if sender.sender_id else "unknown"
    chat_type = msg.chat_type or "unknown"
    lines = [
        "🧭 **身份信息**",
        f"UserId:    `{open_id}`",
        f"ChatType:  {chat_type}",
    ]
    if chat_type == "group":
        lines.append(f"ChatId:    `{msg.chat_id or 'unknown'}`")
    return "\n".join(lines)


async def _cmd_history(session_key: str, ctx: FeishuContext) -> str:
    summaries = await ctx.session_router.store.list_session_history(session_key, limit=10)
    if not summaries:
        return "📚 当前只有一个会话，还没有历史记录。"
    lines = ["📚 **历史会话**"]
    for i, s in enumerate(summaries, 1):
        ts = s.created_at[:19].replace("T", " ") if s.created_at else "unknown"
        short_id = s.session_id.split(":")[-1] if ":" in s.session_id else s.session_id[-8:]
        lines.append(f"{i}. `...{short_id}` — {ts} — {s.message_count} 条消息")
    return "\n".join(lines)


async def _cmd_idle(args: str, session_id: str, ctx: FeishuContext) -> str:
    minutes = _parse_idle_duration(args)
    if minutes is None:
        return "❌ 无法解析时长，请使用如 `30m`、`2h` 或 `off`"

    tree = await ctx.session_router.store.load(session_id)
    if tree is None:
        return "❌ 会话不存在"
    updated_header = tree.header.model_copy(update={"idle_minutes_override": minutes if minutes > 0 else None})
    updated_tree = tree.model_copy(update={"header": updated_header})
    await ctx.session_router.store.save_header(updated_tree)

    if minutes == 0:
        return "✅ 空闲超时已关闭。"
    if minutes < 60:
        unit = f"{minutes} 分钟"
    elif minutes % 60 == 0:
        unit = f"{minutes // 60} 小时"
    else:
        unit = f"{minutes // 60} 小时 {minutes % 60} 分钟"
    return f"✅ 空闲超时已设置为 {unit}。"
