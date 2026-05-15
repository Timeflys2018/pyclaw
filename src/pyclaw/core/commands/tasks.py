"""/tasks slash command: list and kill managed tasks (Phase B)."""

from __future__ import annotations

import logging
from typing import Any

from pyclaw.core.commands.context import CommandContext
from pyclaw.infra.task_inspection import describe, list_all, list_for_owner

logger = logging.getLogger(__name__)

_PROTECTED_KILL_CATEGORIES = frozenset({"consumer", "heartbeat", "archive"})


def _is_admin(ctx: CommandContext) -> bool:
    return ctx.user_id in (ctx.admin_user_ids or [])


def _format_task_line(info: Any) -> str:
    owner_label = info.owner if info.owner else "(system)"
    return (
        f"  {info.task_id} {info.name} [{info.category}] "
        f"state={info.state} owner={owner_label} duration={info.duration_s:.1f}s"
    )


async def cmd_tasks(args: str, ctx: CommandContext) -> None:
    parts = args.strip().split()
    if not parts:
        await ctx.reply("用法: /tasks list [--all] | /tasks kill <task_id> [--confirm]")
        return

    sub = parts[0]
    rest = parts[1:]

    if sub == "list":
        await _cmd_tasks_list(rest, ctx)
    elif sub == "kill":
        await _cmd_tasks_kill(rest, ctx)
    else:
        await ctx.reply(f"❌ 未知子命令: {sub}；支持 list / kill")


async def _cmd_tasks_list(rest: list[str], ctx: CommandContext) -> None:
    task_manager = getattr(ctx.deps, "task_manager", None)
    if task_manager is None:
        await ctx.reply("❌ TaskManager 未初始化（可能是配置问题）")
        return

    show_all = "--all" in rest

    if show_all:
        if not _is_admin(ctx):
            await ctx.reply("❌ --all 仅管理员可用")
            return
        infos = list_all(task_manager)
    else:
        infos = list_for_owner(task_manager, owner=ctx.session_key)

    if not infos:
        await ctx.reply("📋 当前没有运行中的任务")
        return

    lines = ["📋 **当前任务**" + (" (所有)" if show_all else " (当前会话)")]
    for info in infos:
        lines.append(_format_task_line(info))

    await ctx.reply("\n".join(lines))


async def _cmd_tasks_kill(rest: list[str], ctx: CommandContext) -> None:
    if not _is_admin(ctx):
        await ctx.reply("❌ /tasks kill 仅管理员可用")
        return

    if not rest:
        await ctx.reply("用法: /tasks kill <task_id> [--confirm]")
        return

    task_manager = getattr(ctx.deps, "task_manager", None)
    if task_manager is None:
        await ctx.reply("❌ TaskManager 未初始化")
        return

    task_id = rest[0]
    confirm = "--confirm" in rest[1:]

    info = describe(task_manager, task_id)
    if info is None:
        await ctx.reply(f"❌ 任务不存在: {task_id}")
        return

    if not confirm:
        preview = [
            f"将终止 task {info.task_id}",
            f"  name={info.name}",
            f"  category={info.category}",
            f"  owner={info.owner or '(system)'}",
            f"  state={info.state}",
        ]
        if info.category in _PROTECTED_KILL_CATEGORIES:
            preview.append(f"  ⚠️ 此任务类别（{info.category}）被保护，kill 会导致数据不一致")
        preview.append(f"用 /tasks kill {info.task_id} --confirm 执行")
        await ctx.reply("\n".join(preview))
        return

    if info.category in _PROTECTED_KILL_CATEGORIES:
        await ctx.reply(
            f"❌ 拒绝：此任务类别（{info.category}）不可通过 /tasks kill 终止，会导致数据不一致"
        )
        return

    cancelled = await task_manager.cancel(task_id)
    if cancelled:
        await ctx.reply(f"✓ 已取消 task {task_id}")
    else:
        await ctx.reply(f"❌ 任务不存在或已完成: {task_id}")
