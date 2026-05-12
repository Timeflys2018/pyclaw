"""/curator slash command: list / preview / graduate / restore / review-status / review-trigger (Phase D)."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from pyclaw.core.commands._helpers import check_idle
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.curator_admin import (
    ArchivedSopRow,
    SopRow,
    last_review_timestamp,
    list_archived_sops,
    list_auto_sops,
    list_stale_sops,
    preview_graduation,
    restore_sop,
)

logger = logging.getLogger(__name__)

_PREVIEW_CHARS = 100


def _preview(content: str, n: int = _PREVIEW_CHARS) -> str:
    cleaned = content.replace("\n", " ").strip()
    if len(cleaned) <= n:
        return cleaned
    return cleaned[:n].rstrip() + "…"


def _fmt_sop(row: SopRow) -> str:
    last = "—"
    if row.last_used_at is not None:
        try:
            last = datetime.datetime.fromtimestamp(row.last_used_at).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            last = "—"
    return f"  {row.entry_id[:8]} u={row.use_count} last={last} · {_preview(row.content)}"


def _fmt_archived(row: ArchivedSopRow) -> str:
    when = "—"
    if row.archived_at is not None:
        try:
            when = datetime.datetime.fromtimestamp(row.archived_at).strftime("%Y-%m-%d")
        except (OSError, ValueError):
            when = "—"
    reason = row.archive_reason or "—"
    return f"  {row.entry_id[:8]} archived={when} reason={reason} · {_preview(row.content)}"


async def cmd_curator(args: str, ctx: CommandContext) -> None:
    parts = args.strip().split()
    if not parts:
        await ctx.reply(
            "用法: /curator list [--auto|--stale|--archived] | "
            "preview | restore <id> [--confirm] | "
            "review-status | review-trigger [--confirm]"
        )
        return

    sub = parts[0]
    rest = parts[1:]

    if sub == "list":
        await _cmd_curator_list(rest, ctx)
    elif sub == "preview":
        await _cmd_curator_preview(ctx)
    elif sub == "restore":
        await _cmd_curator_restore(rest, ctx)
    elif sub == "review-status":
        await _cmd_curator_review_status(ctx)
    elif sub == "review-trigger":
        await _cmd_curator_review_trigger(rest, ctx)
    else:
        await ctx.reply(
            f"❌ 未知子命令: {sub}；支持 list / preview / restore / review-status / review-trigger"
        )


async def _cmd_curator_list(rest: list[str], ctx: CommandContext) -> None:
    settings = _full_settings(ctx)
    if settings is None:
        await ctx.reply("❌ Settings 未注入")
        return

    session_key = ctx.session_key

    if "--auto" in rest:
        rows = list_auto_sops(settings, session_key=session_key)
        if not rows:
            await ctx.reply("📭 当前会话无活跃自动 SOP")
            return
        lines = [f"🤖 **auto_sop** ({len(rows)} 条)"]
        for row in rows:
            lines.append(_fmt_sop(row))
        await ctx.reply("\n".join(lines))
        return

    if "--stale" in rest:
        rows = list_stale_sops(settings, session_key=session_key)
        if not rows:
            await ctx.reply("📭 当前会话无过期 SOP")
            return
        lines = [f"🍂 **stale SOPs** ({len(rows)} 条)"]
        for row in rows:
            lines.append(_fmt_sop(row))
        await ctx.reply("\n".join(lines))
        return

    if "--archived" in rest:
        arch_rows = list_archived_sops(settings, session_key=session_key)
        if not arch_rows:
            await ctx.reply("📭 当前会话无归档 SOP")
            return
        lines = [f"📦 **archived SOPs** ({len(arch_rows)} 条)"]
        for row in arch_rows:
            lines.append(_fmt_archived(row))
        await ctx.reply("\n".join(lines))
        return

    await ctx.reply("用法: /curator list --auto | --stale | --archived")


async def _cmd_curator_preview(ctx: CommandContext) -> None:
    settings = _full_settings(ctx)
    if settings is None:
        await ctx.reply("❌ Settings 未注入")
        return

    rows = preview_graduation(settings, session_key=ctx.session_key)
    if not rows:
        await ctx.reply("📭 当前会话无符合毕业条件的 SOP")
        return

    lines = [f"🎓 **晋升候选** ({len(rows)} 条)"]
    for row in rows:
        lines.append(_fmt_sop(row))
    lines.append("用 `/curator review-trigger --confirm` 让 LLM review 决策")

    await ctx.reply("\n".join(lines))


async def _cmd_curator_restore(rest: list[str], ctx: CommandContext) -> None:
    if not rest:
        await ctx.reply("用法: /curator restore <id> [--confirm]")
        return

    settings = _full_settings(ctx)
    if settings is None:
        await ctx.reply("❌ Settings 未注入")
        return

    entry_id = rest[0]
    confirm = "--confirm" in rest[1:]

    if not confirm:
        await ctx.reply(
            f"将恢复 archived entry `{entry_id}` (session_key={ctx.session_key})；"
            f"用 `/curator restore {entry_id} --confirm` 执行"
        )
        return

    result = restore_sop(entry_id, settings, session_key=ctx.session_key)
    if result.count == 0:
        await ctx.reply(f"❌ 未找到归档条目: `{entry_id}` (当前会话 scope)")
        return

    await ctx.reply(f"✓ 已恢复 `{entry_id}`（{result.dbs_affected} 个 DB 受影响）")


async def _cmd_curator_review_status(ctx: CommandContext) -> None:
    ts = await last_review_timestamp(ctx.redis_client)
    if ts is None:
        await ctx.reply("📭 尚无 LLM review 记录")
        return

    try:
        iso_ts = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    except (OSError, ValueError):
        iso_ts = str(ts)
    await ctx.reply(f"🕐 上次 LLM review 时间: {iso_ts}")


async def _cmd_curator_review_trigger(rest: list[str], ctx: CommandContext) -> None:
    settings = _full_settings(ctx)
    if settings is None:
        await ctx.reply("❌ Settings 未注入")
        return

    task_manager = getattr(ctx.deps, "task_manager", None)
    if task_manager is None:
        await ctx.reply("❌ TaskManager 未初始化，无法触发异步 review")
        return

    lock_manager = getattr(ctx.deps, "lock_manager", None)
    if lock_manager is None:
        await ctx.reply("❌ LockManager 未初始化")
        return

    confirm = "--confirm" in rest

    if not confirm:
        await ctx.reply(
            "⚠️ 手动触发将消耗 LLM tokens 且覆盖 interval gate。\n"
            "用 `/curator review-trigger --confirm` 执行"
        )
        return

    queue_for_idle = ctx.queue_registry if ctx.channel == "feishu" else ctx.session_queue
    idle_key = ctx.session_id if ctx.channel == "feishu" else ctx.session_id
    if queue_for_idle is not None:
        if await check_idle(queue_for_idle, idle_key, ctx.reply):
            return

    memory_base_dir = _memory_base_dir(settings)
    workspace_base_dir = _workspace_base_dir(ctx)
    llm_client = getattr(ctx.deps, "llm", None)
    memory_store = ctx.memory_store
    l1_index = getattr(memory_store, "_l1", None) if memory_store else None

    owner_label = f"manual:{ctx.session_key}"

    async def _manual_review():
        from pyclaw.core.curator import run_curator_cycle

        return await run_curator_cycle(
            memory_base_dir=memory_base_dir,
            settings=settings.evolution.curator,
            redis_client=ctx.redis_client,
            lock_manager=lock_manager,
            task_manager=task_manager,
            l1_index=l1_index,
            workspace_base_dir=workspace_base_dir,
            llm_client=llm_client,
            mode="review_only",
            force_review=True,
            owner_label=owner_label,
        )

    task_id = task_manager.spawn(
        f"curator-review-manual:{ctx.session_key}",
        _manual_review(),
        category="curator",
        owner=ctx.session_key,
    )

    await ctx.reply(
        f"✓ 已启动 task `{task_id}`；用 `/tasks list` 查看进度"
    )


def _full_settings(ctx: CommandContext) -> Any:
    settings = getattr(ctx.deps, "settings", None)
    if settings is not None:
        return settings
    from pyclaw.infra.settings import load_settings

    try:
        return load_settings()
    except Exception:
        return None


def _memory_base_dir(settings: Any):
    from pathlib import Path

    return Path(settings.memory.base_dir).expanduser()


def _workspace_base_dir(ctx: CommandContext):
    return ctx.workspace_base
