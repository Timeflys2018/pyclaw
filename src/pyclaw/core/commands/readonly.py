from __future__ import annotations

from datetime import UTC, datetime

from pyclaw.core.commands.context import CommandContext
from pyclaw.models.session import MessageEntry


async def cmd_tools(args: str, ctx: CommandContext) -> None:
    registry = ctx.deps.tools
    names = sorted(registry.names())
    if not names:
        await ctx.reply("🛠️ 当前会话无可用工具。")
        return

    safe: list[tuple[str, str]] = []
    side_effect: list[tuple[str, str]] = []
    for name in names:
        tool = registry.get(name)
        if tool is None:
            continue
        entry = (tool.name, (tool.description or "").strip())
        if tool.side_effect:
            side_effect.append(entry)
        else:
            safe.append(entry)

    lines: list[str] = [f"🛠️ **当前可用工具 ({len(names)})**"]
    if safe:
        lines.append("")
        lines.append("**Safe (read-only):**")
        for name, desc in safe:
            lines.append(f"- `{name}` — {desc}" if desc else f"- `{name}`")
    if side_effect:
        lines.append("")
        lines.append("**Side-effect:**")
        for name, desc in side_effect:
            lines.append(f"- `{name}` — {desc}" if desc else f"- `{name}`")
    await ctx.reply("\n".join(lines))


async def cmd_queue(args: str, ctx: CommandContext) -> None:
    provider = ctx.session_queue or ctx.queue_registry
    if provider is None or not hasattr(provider, "queue_position"):
        await ctx.reply("📮 队列信息 unavailable（当前 channel 不支持）。")
        return
    position = provider.queue_position(ctx.session_id if ctx.channel != "web" else _web_cid(ctx))
    if position == 0:
        await ctx.reply("📮 队列空闲（0 pending, idle）。")
    elif position == 1:
        await ctx.reply("📮 队列：1 个任务运行中，0 pending。")
    else:
        await ctx.reply(f"📮 队列：1 个运行中 + {position - 1} pending (total {position}).")


def _web_cid(ctx: CommandContext) -> str:
    return ctx.raw.get("conversation_id") or ctx.session_id


async def cmd_context(args: str, ctx: CommandContext) -> None:
    usage = ctx.last_usage
    if not usage:
        await ctx.reply("📊 尚未有已完成的 run。发送一条消息后再次尝试 /context。")
        return

    input_tok = usage.get("input", 0)
    output_tok = usage.get("output", 0)
    cache_creation = usage.get("cache_creation", 0)
    cache_read = usage.get("cache_read", 0)

    lines = ["📊 **Context usage (last completed run)**"]
    lines.append(f"- Input tokens:       `{input_tok:,}`")
    lines.append(f"- Output tokens:      `{output_tok:,}`")
    lines.append(f"- Cache created:      `{cache_creation:,}`")
    lines.append(f"- Cache read:         `{cache_read:,}`")

    system_zone = 0
    dynamic_zone = 0
    try:
        system_zone = int(ctx.settings.agent.prompt_budget.system_zone_tokens)
        dynamic_zone = int(ctx.settings.agent.prompt_budget.dynamic_zone_tokens)
    except (AttributeError, TypeError, ValueError):
        system_zone = 0
        dynamic_zone = 0
    if system_zone > 0 or dynamic_zone > 0:
        lines.append("")
        lines.append("🎯 **Prompt budget reservations** (frozen+dynamic zones, per turn)")
        if system_zone > 0:
            lines.append(
                f"- System zone (frozen): `{system_zone:,}` tokens (identity/tools/skills/workspace/L1)"
            )
        if dynamic_zone > 0:
            lines.append(f"- Dynamic zone: `{dynamic_zone:,}` tokens (memory search injection)")
        lines.append(
            "ℹ Input tokens above include frozen + per-turn + dynamic + message history; detailed per-zone breakdown is in server logs (`token_usage` line)."
        )

    await ctx.reply("\n".join(lines))


async def cmd_resume(args: str, ctx: CommandContext) -> None:
    arg = args.strip()
    store = ctx.deps.session_store

    if arg.lower() == "current":
        suffix = ctx.session_id.rsplit(":", 1)[-1][-8:]
        await ctx.reply(f"✓ 当前已在此 session (`...{suffix}`)。")
        return

    history = await store.list_session_history(ctx.session_key, limit=20)
    recent = history[:5]

    if not arg:
        if not recent:
            await ctx.reply("📭 无历史 session。发送一条消息即可创建新 session。")
            return
        lines = ["📜 **最近 5 个历史 sessions** (使用 `/resume <编号>` 切换)"]
        for i, h in enumerate(recent, start=1):
            suffix = h.session_id.rsplit(":", 1)[-1][-8:]
            rel = _relative_time(h.created_at)
            lines.append(f"[{i}] `...{suffix}` — {rel}，{h.message_count} 条消息")
        await ctx.reply("\n".join(lines))
        return

    target: str | None = None
    is_small_index = arg.isdigit() and 1 <= len(arg) <= 2
    if is_small_index:
        idx = int(arg)
        if 1 <= idx <= len(recent):
            target = recent[idx - 1].session_id
        else:
            await ctx.reply(
                f"⚠ 索引 {idx} 无效（范围 1-{len(recent) or 0}）。"
                "使用 `/resume` 查看可用 sessions。"
            )
            return
    else:
        matches = [h.session_id for h in history if h.session_id.endswith(arg)]
        if len(matches) == 1:
            target = matches[0]
        elif len(matches) == 0:
            await ctx.reply(
                f"⚠ 找不到后缀为 `{arg}` 的 session。使用 `/resume` 查看可用 sessions。"
            )
            return
        else:
            lines = [f"⚠ 后缀 `{arg}` 匹配多个 sessions（共 {len(matches)}）："]
            for m in matches[:10]:
                suffix = m.rsplit(":", 1)[-1][-12:]
                lines.append(f"  - `...{suffix}`")
            lines.append("请使用更长的后缀或 `/resume <编号>`。")
            await ctx.reply("\n".join(lines))
            return

    tree = await store.load(target)
    if tree is None:
        await ctx.reply("⚠ 目标 session 数据已过期（超过 Redis TTL），无法切换。")
        return

    await store.set_current_session_id(ctx.session_key, target)

    suffix = target.rsplit(":", 1)[-1][-8:]
    lines = [f"✓ 已切换到 session `...{suffix}`。"]
    tail = _format_message_tail(tree, n=5)
    if tail:
        lines.append("")
        lines.append("📜 最近消息：")
        lines.extend(tail)
    else:
        lines.append("（此 session 暂无可显示的对话消息。）")
    await ctx.reply("\n".join(lines))


def _format_message_tail(tree, n: int = 5) -> list[str]:
    out: list[str] = []
    recent_ids = tree.order[-(n * 3) :]
    msgs: list[MessageEntry] = []
    for eid in recent_ids:
        entry = tree.entries.get(eid)
        if isinstance(entry, MessageEntry) and entry.role in ("user", "assistant"):
            msgs.append(entry)
    msgs = msgs[-n:]
    for m in msgs:
        content = m.content if isinstance(m.content, str) else _content_to_str(m.content)
        content = content.replace("\n", " ")
        if len(content) > 80:
            content = content[:80] + "…"
        role = "user" if m.role == "user" else "asst"
        out.append(f"  {role:<4} > {content}")
    return out


def _content_to_str(content) -> str:
    parts: list[str] = []
    for block in content or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return " ".join(parts)


def _relative_time(iso_ts: str) -> str:
    if not iso_ts:
        return "时间未知"
    try:
        dt = datetime.fromisoformat(iso_ts)
    except ValueError:
        return iso_ts
    now = datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    elapsed = (now - dt).total_seconds()
    if elapsed < 60:
        return "刚刚"
    if elapsed < 3600:
        return f"{int(elapsed // 60)} 分钟前"
    if elapsed < 86400:
        return f"{int(elapsed // 3600)} 小时前"
    return f"{int(elapsed // 86400)} 天前"
