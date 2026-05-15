"""/memory slash command: list / search / stats (Phase C)."""

from __future__ import annotations

import logging

from pyclaw.core.commands.context import CommandContext
from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.inspection import (
    list_for_user,
    search_for_user,
    stats_for_user,
)

logger = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50
_PREVIEW_CHARS = 120


def _format_ts(ts: float | None) -> str:
    if ts is None:
        return "—"
    import datetime

    try:
        return datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError):
        return "—"


def _preview(content: str, n: int = _PREVIEW_CHARS) -> str:
    cleaned = content.replace("\n", " ").strip()
    if len(cleaned) <= n:
        return cleaned
    return cleaned[:n].rstrip() + "…"


def _format_entry(entry: MemoryEntry) -> str:
    eid = entry.id[:8]
    layer = entry.layer
    use_count = entry.use_count
    last_used = _format_ts(entry.last_used_at or entry.updated_at)
    return f"  [{layer}] {eid} u={use_count} {last_used} · {_preview(entry.content)}"


async def cmd_memory(args: str, ctx: CommandContext) -> None:
    parts = args.strip().split()
    if not parts:
        await ctx.reply(
            "用法: /memory list [--facts|--procedures|--all] [--limit N] | "
            "/memory search <query> | /memory stats"
        )
        return

    sub = parts[0]
    rest = parts[1:]

    if sub == "list":
        await _cmd_memory_list(rest, ctx)
    elif sub == "search":
        await _cmd_memory_search(rest, ctx)
    elif sub == "stats":
        await _cmd_memory_stats(ctx)
    else:
        await ctx.reply(f"❌ 未知子命令: {sub}；支持 list / search / stats")


def _parse_limit(rest: list[str], default: int = _DEFAULT_LIMIT) -> int:
    for i, tok in enumerate(rest):
        if tok == "--limit" and i + 1 < len(rest):
            try:
                n = int(rest[i + 1])
                if n > 0:
                    return min(n, 200)
            except ValueError:
                pass
    return default


def _parse_kind(rest: list[str]) -> str:
    if "--facts" in rest:
        return "facts"
    if "--procedures" in rest:
        return "procedures"
    return "all"


async def _cmd_memory_list(rest: list[str], ctx: CommandContext) -> None:
    if ctx.memory_store is None:
        await ctx.reply("❌ Memory store 未初始化（可能是配置问题）")
        return

    kind = _parse_kind(rest)
    limit = _parse_limit(rest)

    try:
        entries = await list_for_user(ctx.memory_store, ctx.session_key, kind=kind, limit=limit)
    except ValueError as exc:
        await ctx.reply(f"❌ {exc}")
        return

    if not entries:
        await ctx.reply(f"📭 当前会话无 {kind} 记忆")
        return

    lines = [f"📚 **{kind}** ({len(entries)} / 上限 {limit})"]
    for entry in entries:
        lines.append(_format_entry(entry))

    await ctx.reply("\n".join(lines))


async def _cmd_memory_search(rest: list[str], ctx: CommandContext) -> None:
    if ctx.memory_store is None:
        await ctx.reply("❌ Memory store 未初始化（可能是配置问题）")
        return

    query = " ".join(rest).strip()
    if not query:
        await ctx.reply("用法: /memory search <query>")
        return

    try:
        entries = await search_for_user(ctx.memory_store, ctx.session_key, query)
    except ValueError as exc:
        await ctx.reply(f"❌ {exc}")
        return

    if not entries:
        await ctx.reply(f"🔍 无匹配结果: `{query}`")
        return

    lines = [f"🔍 **搜索结果** (query=`{query}`, {len(entries)} 条)"]
    for entry in entries:
        lines.append(_format_entry(entry))

    await ctx.reply("\n".join(lines))


async def _cmd_memory_stats(ctx: CommandContext) -> None:
    if ctx.memory_store is None:
        await ctx.reply("❌ Memory store 未初始化（可能是配置问题）")
        return

    try:
        stats = await stats_for_user(ctx.memory_store, ctx.session_key)
    except ValueError as exc:
        await ctx.reply(f"❌ {exc}")
        return

    lines = [
        "📊 **Memory Stats (当前会话)**",
        f"  L1 (Redis hot index):   {stats.get('l1', 0)}",
        f"  L2 (Facts):             {stats.get('l2', 0)}",
        f"  L3 (Procedures):        {stats.get('l3', 0)}",
        f"  L4 (Session Archives):  {stats.get('l4', 0)}",
    ]

    await ctx.reply("\n".join(lines))
