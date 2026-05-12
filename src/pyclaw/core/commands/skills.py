"""/skills slash command: list / search / install / check (Phase E)."""

from __future__ import annotations

import logging
from pathlib import Path

from pyclaw.core.commands._helpers import check_idle
from pyclaw.core.commands.context import CommandContext
from pyclaw.skills.management import (
    check_eligibility,
    install,
    list_discovered,
    search_hub,
)

logger = logging.getLogger(__name__)


async def cmd_skills(args: str, ctx: CommandContext) -> None:
    parts = args.strip().split()
    if not parts:
        await ctx.reply(
            "用法: /skills list | search <q> | install <slug> [--version V] [--confirm] | check [name]"
        )
        return

    sub = parts[0]
    rest = parts[1:]

    if sub == "list":
        await _cmd_skills_list(ctx)
    elif sub == "search":
        await _cmd_skills_search(rest, ctx)
    elif sub == "install":
        await _cmd_skills_install(rest, ctx)
    elif sub == "check":
        await _cmd_skills_check(rest, ctx)
    else:
        await ctx.reply(
            f"❌ 未知子命令: {sub}；支持 list / search / install / check"
        )


def _workspace_path(ctx: CommandContext) -> Path:
    return Path(ctx.workspace_base) / ctx.workspace_id


async def _cmd_skills_list(ctx: CommandContext) -> None:
    settings = ctx.settings
    skill_settings = getattr(settings, "skills", None)
    skills = list_discovered(_workspace_path(ctx), skill_settings)
    if not skills:
        await ctx.reply("📭 当前 workspace 未发现任何 skills")
        return

    eligible = [s for s in skills if s.eligible]
    ineligible = [s for s in skills if not s.eligible]

    lines = [f"🧰 **Skills** ({len(skills)} total)"]
    if eligible:
        lines.append("")
        lines.append("**✅ Eligible:**")
        for s in eligible:
            emoji = s.emoji or "•"
            lines.append(f"  {emoji} `{s.name}` — {s.description}")
    if ineligible:
        lines.append("")
        lines.append("**❌ Ineligible:**")
        for s in ineligible:
            lines.append(f"  ⚠️  `{s.name}` — {s.description}")

    await ctx.reply("\n".join(lines))


async def _cmd_skills_search(rest: list[str], ctx: CommandContext) -> None:
    query = " ".join(rest).strip()
    if not query:
        await ctx.reply("用法: /skills search <query>")
        return

    try:
        results = await search_hub(query)
    except RuntimeError as exc:
        await ctx.reply(f"❌ {exc}")
        return

    if not results:
        await ctx.reply(f"📭 ClawHub 未找到匹配 `{query}`")
        return

    lines = [f"🔍 **ClawHub 搜索** ({len(results)} 条, query=`{query}`)"]
    for r in results:
        lines.append(f"  `{r.slug}` @ {r.latest_version} — {r.description}")

    await ctx.reply("\n".join(lines))


def _parse_version(rest: list[str]) -> str | None:
    for i, tok in enumerate(rest):
        if tok == "--version" and i + 1 < len(rest):
            return rest[i + 1]
    return None


async def _cmd_skills_install(rest: list[str], ctx: CommandContext) -> None:
    if not rest:
        await ctx.reply("用法: /skills install <slug> [--version V] [--confirm]")
        return

    slug = rest[0]
    version = _parse_version(rest[1:])
    confirm = "--confirm" in rest[1:]

    if not confirm:
        version_str = f"@ {version}" if version else "(latest)"
        scope = "此会话对应的 workspace"
        if ctx.channel == "feishu":
            scope = "飞书（可能影响整个群/会话）"
        elif ctx.channel == "web":
            scope = "当前 Web workspace"
        await ctx.reply(
            f"⚠️ 将安装 skill `{slug}` {version_str}\n"
            f"  scope: {scope}\n"
            f"  workspace: {_workspace_path(ctx)}\n"
            f"用 `/skills install {slug} {'--version ' + version + ' ' if version else ''}--confirm` 执行"
        )
        return

    queue_for_idle = ctx.queue_registry if ctx.channel == "feishu" else ctx.session_queue
    if queue_for_idle is not None:
        if await check_idle(queue_for_idle, ctx.session_id, ctx.reply):
            return

    install_dir = _workspace_path(ctx) / ".claude" / "skills"
    install_dir.mkdir(parents=True, exist_ok=True)

    result = await install(slug, version, install_dir)
    if result.ok:
        await ctx.reply(f"✓ 已安装 `{slug}` → `{result.dest}`")
    else:
        await ctx.reply(f"❌ 安装失败: {result.error}")


async def _cmd_skills_check(rest: list[str], ctx: CommandContext) -> None:
    settings = ctx.settings
    skill_settings = getattr(settings, "skills", None)
    name = rest[0] if rest else None
    reports = check_eligibility(_workspace_path(ctx), skill_settings, name=name)

    if not reports:
        if name:
            await ctx.reply(f"❌ Skill `{name}` 未发现")
        else:
            await ctx.reply("📭 当前 workspace 未发现任何 skills")
        return

    eligible_count = sum(1 for r in reports if r.ok)
    lines = [f"🧪 **Eligibility Check** ({eligible_count}/{len(reports)} eligible)"]
    for r in reports:
        if r.ok:
            lines.append(f"  ✅ `{r.name}`")
        else:
            lines.append(f"  ❌ `{r.name}`: {'; '.join(r.issues)}")

    await ctx.reply("\n".join(lines))
