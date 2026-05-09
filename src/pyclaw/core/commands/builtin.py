from __future__ import annotations

import logging

from pyclaw.core.commands._helpers import (
    format_session_status,
    parse_idle_duration,
    run_extract,
)
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec

logger = logging.getLogger(__name__)


async def cmd_new(args: str, ctx: CommandContext) -> None:
    await ctx.session_router.rotate(ctx.session_key, ctx.workspace_id)
    await ctx.reply("✨ 新会话已开始，之前的对话已归档。")
    followup = args.strip()
    if followup:
        await ctx.dispatch_user_message(followup)


async def cmd_reset(args: str, ctx: CommandContext) -> None:
    await ctx.session_router.rotate(ctx.session_key, ctx.workspace_id)
    await ctx.reply("🔄 会话已重置，之前的对话已归档。")
    followup = args.strip()
    if followup:
        await ctx.dispatch_user_message(followup)


async def cmd_status(args: str, ctx: CommandContext) -> None:
    text = await format_session_status(ctx.session_key, ctx.session_id, ctx.deps)
    await ctx.reply(text)


async def cmd_whoami(args: str, ctx: CommandContext) -> None:
    if ctx.channel == "feishu":
        event = ctx.raw["feishu_event"]
        if not event.event or not event.event.sender or not event.event.message:
            await ctx.reply("❓ 无法获取身份信息")
            return
        sender = event.event.sender
        msg = event.event.message
        open_id = (
            (sender.sender_id.open_id or "unknown")
            if sender.sender_id
            else "unknown"
        )
        chat_type = msg.chat_type or "unknown"
        lines = [
            "🧭 **身份信息**",
            f"UserId:    `{open_id}`",
            f"ChatType:  {chat_type}",
        ]
        if chat_type == "group":
            lines.append(f"ChatId:    `{msg.chat_id or 'unknown'}`")
        await ctx.reply("\n".join(lines))
    else:
        lines = [
            "🧭 **身份信息**",
            f"UserId:    `{ctx.user_id}`",
            f"Channel:   {ctx.channel}",
        ]
        await ctx.reply("\n".join(lines))


async def cmd_history(args: str, ctx: CommandContext) -> None:
    summaries = await ctx.deps.session_store.list_session_history(
        ctx.session_key, limit=10
    )
    if not summaries:
        await ctx.reply("📚 当前只有一个会话，还没有历史记录。")
        return
    lines = ["📚 **历史会话**"]
    for i, s in enumerate(summaries, 1):
        ts = s.created_at[:19].replace("T", " ") if s.created_at else "unknown"
        short_id = (
            s.session_id.split(":")[-1] if ":" in s.session_id else s.session_id[-8:]
        )
        lines.append(f"{i}. `...{short_id}` — {ts} — {s.message_count} 条消息")
    await ctx.reply("\n".join(lines))


async def cmd_idle(args: str, ctx: CommandContext) -> None:
    minutes = parse_idle_duration(args)
    if minutes is None:
        await ctx.reply("❌ 无法解析时长，请使用如 `30m`、`2h` 或 `off`")
        return

    tree = await ctx.session_router.store.load(ctx.session_id)
    if tree is None:
        await ctx.reply("❌ 会话不存在")
        return
    updated_header = tree.header.model_copy(
        update={"idle_minutes_override": minutes if minutes > 0 else None}
    )
    updated_tree = tree.model_copy(update={"header": updated_header})
    await ctx.session_router.store.save_header(updated_tree)

    if minutes == 0:
        await ctx.reply("✅ 空闲超时已关闭。")
        return
    if minutes < 60:
        unit = f"{minutes} 分钟"
    elif minutes % 60 == 0:
        unit = f"{minutes // 60} 小时"
    else:
        unit = f"{minutes // 60} 小时 {minutes % 60} 分钟"
    await ctx.reply(f"✅ 空闲超时已设置为 {unit}。")


async def cmd_extract(args: str, ctx: CommandContext) -> None:
    from pyclaw.core.sop_extraction import format_extraction_result_zh

    result = await run_extract(
        redis_client=ctx.redis_client,
        memory_store=ctx.memory_store,
        session_store=ctx.deps.session_store,
        llm_client=ctx.deps.llm,
        session_id=ctx.session_id,
        settings=ctx.evolution_settings,
        nudge_hook=ctx.nudge_hook,
    )
    if result is None:
        await ctx.reply(
            "⏳ 学习超时（>15 秒）已中止，候选数据已保留，1 分钟后可再次 /extract。"
        )
        return
    if result.skip_reason == "disabled":
        await ctx.reply("⚠️ 自我进化功能未启用。")
        return
    if result.skip_reason == "rate_limited":
        await ctx.reply("⏱ 学习触发过于频繁，请 1 分钟后再试。")
        return
    await ctx.reply(format_extraction_result_zh(result))


async def cmd_help(args: str, ctx: CommandContext) -> None:
    from pyclaw.core.commands.registry import get_default_registry

    registry = ctx.registry if ctx.registry is not None else get_default_registry()
    grouped = registry.list_by_category()

    has_idle_locked = False
    lines = ["📖 PyClaw 命令帮助", ""]
    for category in sorted(grouped.keys()):
        lines.append(f"📂 {category}")
        for spec in grouped[category]:
            args_part = spec.args_hint
            args_padded = args_part.ljust(22) if args_part else "".ljust(22)
            help_part = spec.help_text
            if spec.aliases:
                alias_str = ", ".join(spec.aliases)
                help_part = f"{help_part} (别名: {alias_str})"
            if spec.requires_idle:
                help_part = f"{help_part} 🔒"
                has_idle_locked = True
            lines.append(f"  {spec.name} {args_padded}{help_part}")
        lines.append("")

    lines.append("⚡ Runtime Operations（运行时控制，不进队列）")
    lines.append(f"  /stop {''.ljust(22)}停止当前运行")
    lines.append("")

    if has_idle_locked:
        lines.append("提示：🔒 标记的命令需要 runner 闲置时执行")

    await ctx.reply("\n".join(lines).rstrip() + "\n")


def register_builtin_commands(registry: CommandRegistry) -> None:
    registry.register(
        CommandSpec(
            name="/new",
            handler=cmd_new,
            category="session",
            help_text="开启新会话（可选附带初始消息）",
            args_hint="[消息]",
            channels=ALL_CHANNELS,
            requires_idle=True,
        )
    )
    registry.register(
        CommandSpec(
            name="/reset",
            handler=cmd_reset,
            category="session",
            help_text="重置会话（同 /new，提示语不同）",
            args_hint="[消息]",
            channels=ALL_CHANNELS,
            requires_idle=True,
        )
    )
    registry.register(
        CommandSpec(
            name="/status",
            handler=cmd_status,
            category="inspection",
            help_text="查看当前会话状态",
            channels=ALL_CHANNELS,
        )
    )
    registry.register(
        CommandSpec(
            name="/whoami",
            handler=cmd_whoami,
            category="inspection",
            help_text="查看身份信息",
            channels=ALL_CHANNELS,
        )
    )
    registry.register(
        CommandSpec(
            name="/history",
            handler=cmd_history,
            category="inspection",
            help_text="查看历史会话列表",
            channels=ALL_CHANNELS,
        )
    )
    registry.register(
        CommandSpec(
            name="/help",
            handler=cmd_help,
            category="inspection",
            help_text="显示此帮助",
            channels=ALL_CHANNELS,
        )
    )
    registry.register(
        CommandSpec(
            name="/idle",
            handler=cmd_idle,
            category="config",
            help_text="设置空闲超时",
            args_hint="<时长>",
            channels=ALL_CHANNELS,
        )
    )
    registry.register(
        CommandSpec(
            name="/extract",
            handler=cmd_extract,
            category="evolution",
            help_text="手动触发 SOP 提取",
            aliases=["/learn"],
            channels=ALL_CHANNELS,
            requires_idle=True,
        )
    )
