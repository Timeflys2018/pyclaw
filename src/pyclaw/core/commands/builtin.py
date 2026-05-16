from __future__ import annotations

import logging
from datetime import UTC
from typing import Any

from pyclaw.core.commands._helpers import (
    format_session_status,
    list_available_models,
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
    if ctx.channel == "feishu":
        channel_settings = ctx.settings.channels.feishu
    elif ctx.channel == "web":
        channel_settings = ctx.settings.channels.web
    else:
        channel_settings = None
    default_tier = (
        getattr(channel_settings, "default_permission_tier", None) or "approval"
    )

    text = await format_session_status(
        ctx.session_key,
        ctx.session_id,
        ctx.deps,
        worker_registry=ctx.worker_registry,
        gateway_router=ctx.gateway_router,
        redis_client=ctx.redis_client,
        channel=ctx.channel,
        channel_default_tier=default_tier,
    )
    await ctx.reply(text)


async def cmd_whoami(args: str, ctx: CommandContext) -> None:
    if ctx.channel == "feishu":
        event = ctx.raw["feishu_event"]
        if not event.event or not event.event.sender or not event.event.message:
            await ctx.reply("❓ 无法获取身份信息")
            return
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
        await ctx.reply("\n".join(lines))
    else:
        lines = [
            "🧭 **身份信息**",
            f"UserId:    `{ctx.user_id}`",
            f"Channel:   {ctx.channel}",
        ]
        await ctx.reply("\n".join(lines))


async def cmd_history(args: str, ctx: CommandContext) -> None:
    summaries = await ctx.deps.session_store.list_session_history(ctx.session_key, limit=10)
    if not summaries:
        await ctx.reply("📚 当前只有一个会话，还没有历史记录。")
        return
    lines = ["📚 **历史会话**"]
    for i, s in enumerate(summaries, 1):
        ts = s.created_at[:19].replace("T", " ") if s.created_at else "unknown"
        short_id = s.session_id.split(":")[-1] if ":" in s.session_id else s.session_id[-8:]
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


async def cmd_tier(args: str, ctx: CommandContext) -> None:
    from pyclaw.core.commands.tier_store import (
        get_session_tier,
        parse_tier_arg,
        set_session_tier,
    )

    arg = args.strip()
    if ctx.channel == "feishu":
        channel_settings = ctx.settings.channels.feishu
    elif ctx.channel == "web":
        channel_settings = ctx.settings.channels.web
    else:
        channel_settings = None
    default_tier = (
        getattr(channel_settings, "default_permission_tier", None) or "approval"
    )

    is_web = ctx.channel == "web"

    if not arg:
        if is_web:
            await ctx.reply(
                "🛡 **Permission tier (Web)**\n"
                f"部署默认: `{default_tier}`\n"
                "_当前生效 tier 由输入框左下角的 dropdown 决定 (per-message)。_\n"
                "切换请直接点 dropdown 或用 ⌘K 命令面板的 'Switch to ... mode'。"
            )
            return

        current = await get_session_tier(ctx.redis_client, ctx.session_key)
        effective = current or default_tier
        source = "session override" if current else "deployment default"
        lines = [
            "🛡 **Permission tier**",
            f"当前: `{effective}` ({source})",
            f"部署默认: `{default_tier}`",
            "",
            "切换: `/tier read-only` | `/tier approval` | `/tier yolo`",
        ]
        await ctx.reply("\n".join(lines))
        return

    if is_web:
        await ctx.reply(
            "ℹ️ Web channel 的 tier 由输入框 dropdown 控制 (per-message),`/tier <name>` 在此处无效。\n"
            "请直接点输入框左下角的 dropdown,或用 ⌘K → 'Switch to ... mode'。"
        )
        return

    new_tier = parse_tier_arg(arg)
    if new_tier is None:
        await ctx.reply(
            "❌ 无效 tier。可选: `read-only`、`approval`、`yolo`(支持 `ro`/`ap`/`y` 缩写)"
        )
        return

    previous = await get_session_tier(ctx.redis_client, ctx.session_key)
    ok = await set_session_tier(ctx.redis_client, ctx.session_key, new_tier)
    if not ok:
        await ctx.reply(
            "⚠️ Redis 不可用,无法持久化 tier 偏好。"
            "可改 `pyclaw.json` 的 `channels.feishu.defaultPermissionTier` 后重启。"
        )
        return

    audit = getattr(ctx.deps, "audit_logger", None)
    if audit is not None and previous != new_tier:
        try:
            audit.log_tier_change(
                session_id=ctx.session_id,
                channel=ctx.channel,
                from_tier=previous,
                to_tier=new_tier,
                user_id=ctx.user_id or None,
            )
        except Exception:
            logger.warning("audit log_tier_change failed", exc_info=True)

    if previous == new_tier:
        await ctx.reply(f"🛡 已是 `{new_tier}` tier,无需切换。")
    else:
        previous_str = f"`{previous}`" if previous else f"`{default_tier}`(默认)"
        await ctx.reply(
            f"✅ Permission tier: {previous_str} → `{new_tier}`\n"
            f"_下条消息生效。重启 PyClaw 后回退到部署默认。_"
        )


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
        await ctx.reply("⏳ 学习超时（>15 秒）已中止，候选数据已保留，1 分钟后可再次 /extract。")
        return
    if result.skip_reason == "disabled":
        await ctx.reply("⚠️ 自我进化功能未启用。")
        return
    if result.skip_reason == "rate_limited":
        await ctx.reply("⏱ 学习触发过于频繁，请 1 分钟后再试。")
        return
    await ctx.reply(format_extraction_result_zh(result))


async def cmd_model(args: str, ctx: CommandContext) -> None:
    from pyclaw.core.agent.llm import (
        LLMError,
        LLMErrorCode,
        model_supports_input,
        resolve_provider_for_model,
    )
    from pyclaw.core.commands._helpers import list_available_models_with_modalities
    from pyclaw.models import ModelChangeEntry, generate_entry_id, now_iso

    target = args.strip()
    available = list_available_models(ctx.agent_settings)

    if not target:
        tree = await ctx.deps.session_store.load(ctx.session_id)
        current = (
            tree.header.model_override if tree and tree.header.model_override else None
        ) or getattr(ctx.deps.llm, "default_model", "(unknown)")

        lines = [f"🤖 当前模型: `{current}`", ""]
        available_with_modalities = list_available_models_with_modalities(ctx.agent_settings)
        if available_with_modalities:
            lines.append("可用模型:")
            for provider, pairs in sorted(available_with_modalities.items()):
                lines.append(f"  📦 {provider}")
                for mid, modalities in pairs:
                    non_text_inputs = sorted(modalities.input - {"text"})
                    tag = f" ({', '.join(non_text_inputs)})" if non_text_inputs else ""
                    lines.append(f"    • {mid}{tag}")
        else:
            lines.append("⚠️ 配置中尚未声明可用模型列表 (agent.providers.<name>.models)。")
        await ctx.reply("\n".join(lines))
        return

    if ctx.agent_settings.providers:
        try:
            resolve_provider_for_model(
                target,
                ctx.agent_settings.providers,
                default_provider=None,
                unknown_prefix_policy="fail",
            )
        except LLMError as exc:
            if exc.code == LLMErrorCode.PROVIDER_NOT_FOUND:
                await ctx.reply(f"❌ {exc}")
                return
            raise

    tree = await ctx.deps.session_store.load(ctx.session_id)
    if tree is None:
        await ctx.reply("❌ 会话不存在")
        return

    updated_header = tree.header.model_copy(update={"model_override": target})
    updated_tree = tree.model_copy(update={"header": updated_header})
    await ctx.deps.session_store.save_header(updated_tree)

    provider = "unknown"
    for prov, models in available.items():
        if target in models:
            provider = prov
            break

    entry = ModelChangeEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        provider=provider,
        model_id=target,
    )
    await ctx.deps.session_store.append_entry(ctx.session_id, entry, leaf_id=entry.id)

    has_vision = False
    if ctx.agent_settings.providers:
        try:
            has_vision = model_supports_input(
                target,
                ctx.agent_settings.providers,
                "image",
                default_provider=None,
                unknown_prefix_policy="fail",
            )
        except LLMError:
            has_vision = False

    warning = "\nℹ️ 该模型不支持图片处理" if not has_vision else ""
    await ctx.reply(f"✓ 模型已切换为 `{target}`（下次对话生效）{warning}")


async def cmd_compact(args: str, ctx: CommandContext) -> None:
    import asyncio as _asyncio

    from pyclaw.core.agent.compaction import estimate_messages_tokens
    from pyclaw.core.hooks import CompactionContext
    from pyclaw.models import CompactionEntry, generate_entry_id, now_iso

    cooldown_key = f"pyclaw:compact_cooldown:{ctx.session_id}"
    if ctx.redis_client is not None:
        try:
            acquired = await ctx.redis_client.set(cooldown_key, "1", nx=True, ex=60)
            if not acquired:
                await ctx.reply("⏱ /compact 冷却中（60s 内只能触发一次）")
                return
        except Exception:
            logger.warning("compact cooldown check failed; proceeding without lock", exc_info=True)

    tree = await ctx.deps.session_store.load(ctx.session_id)
    if tree is None:
        await ctx.reply("❌ 会话不存在")
        return

    base_messages = tree.build_session_context()
    if not base_messages:
        await ctx.reply("📭 当前会话没有可压缩的消息")
        return

    config = ctx.deps.config
    model_max_output: int | None = None
    try:
        from litellm import get_model_info

        _info = get_model_info(getattr(ctx.deps.llm, "default_model", "gpt-4o"))
        model_max_output = _info.get("max_output_tokens") or _info.get("max_tokens")
    except Exception:
        pass

    history_budget = config.prompt_budget.compute_history_budget(
        config.context_window, model_max_output=model_max_output
    )

    focus = args.strip()
    engine_to_use = ctx.deps.context_engine
    if focus:
        engine_to_use = _wrap_context_engine_with_focus(ctx.deps.context_engine, focus)

    compaction_ctx = CompactionContext(
        session_id=ctx.session_id,
        workspace_id=ctx.workspace_id,
        agent_id=tree.header.agent_id,
        message_count=len(base_messages),
        tokens_before=estimate_messages_tokens(base_messages),
    )
    await ctx.deps.hooks.notify_before_compaction(compaction_ctx)

    abort_event = _asyncio.Event()
    try:
        result = await engine_to_use.compact(
            session_id=ctx.session_id,
            messages=base_messages,
            token_budget=history_budget,
            force=True,
            abort_event=abort_event,
            model=config.compaction.model,
        )
    except Exception as exc:
        logger.exception("compact handler failed")
        await ctx.deps.hooks.notify_after_compaction(
            compaction_ctx,
            _failed_compact_result(str(exc)),
        )
        await ctx.reply(f"⚠️ 压缩失败：{type(exc).__name__}: {exc}")
        return

    compaction_ctx.tokens_before = result.tokens_before
    await ctx.deps.hooks.notify_after_compaction(compaction_ctx, result)

    if not result.ok:
        await ctx.reply(f"⚠️ 压缩失败：{result.reason or 'unknown'}")
        return

    if not result.compacted:
        await ctx.reply(f"ℹ️ 无需压缩：{result.reason or 'within-budget'}")
        return

    if result.summary:
        comp_entry = CompactionEntry(
            id=generate_entry_id(set(tree.entries.keys())),
            parent_id=tree.leaf_id,
            timestamp=now_iso(),
            summary=result.summary,
            first_kept_entry_id=tree.leaf_id or "",
            tokens_before=result.tokens_before,
        )
        await ctx.deps.session_store.append_entry(ctx.session_id, comp_entry, leaf_id=comp_entry.id)

    tokens_saved = max(0, result.tokens_before - (result.tokens_after or 0))
    await ctx.reply(f"✓ 压缩完成，节省约 {tokens_saved} tokens")


def _wrap_context_engine_with_focus(engine: Any, focus: str) -> Any:
    class _FocusedEngine:
        def __init__(self) -> None:
            self._inner = engine
            self._focus = focus

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        async def compact(self, **kwargs: Any) -> Any:
            messages = kwargs.get("messages") or []
            focus_msg = {
                "role": "system",
                "content": f"Compaction focus from user: {self._focus}",
            }
            kwargs["messages"] = [focus_msg, *messages]
            return await self._inner.compact(**kwargs)

    return _FocusedEngine()


def _failed_compact_result(error: str) -> Any:
    from pyclaw.models import CompactResult

    return CompactResult(
        ok=False,
        compacted=False,
        reason=f"handler exception: {error}",
        reason_code="handler_error",
    )


async def cmd_export(args: str, ctx: CommandContext) -> None:
    import secrets
    from datetime import datetime
    from pathlib import Path as _Path

    from pyclaw.core.session_export import (
        render_session_json,
        render_session_markdown,
    )

    tokens = args.lower().split()
    inline = "inline" in tokens
    fmt = "markdown"
    if "json" in tokens:
        fmt = "json"
    elif "markdown" in tokens or "md" in tokens:
        fmt = "markdown"

    tree = await ctx.deps.session_store.load(ctx.session_id)
    if tree is None:
        await ctx.reply("❌ 会话不存在")
        return

    if fmt == "json":
        rendered = render_session_json(tree)
        ext = "json"
    else:
        rendered = render_session_markdown(tree)
        ext = "md"

    if inline:
        truncated = _truncate_utf8_bytes(rendered, 8192)
        await ctx.reply(truncated)
        return

    workspace_path: _Path | str | None = None
    raw = ctx.raw or {}
    workspace_path = raw.get("tool_workspace_path")
    if workspace_path is None:
        workspace_path = ctx.workspace_base / f"{ctx.channel}_{ctx.user_id}"

    workspace_path = _Path(workspace_path)
    exports_dir = workspace_path / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    workspace_resolved = workspace_path.resolve()
    exports_resolved = exports_dir.resolve()
    if not _path_is_under(exports_resolved, workspace_resolved):
        await ctx.reply(
            f"⚠️ 导出失败：exports 目录 `{exports_resolved}` 逃出了工作区 `{workspace_resolved}`（疑似符号链接）"
        )
        return

    rand = secrets.token_hex(4)
    utc_iso = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    filename = f"session-{rand}-{utc_iso}.{ext}"
    target = exports_dir / filename

    target.write_text(rendered, encoding="utf-8")

    resolved = target.resolve()
    if not _path_is_under(resolved, workspace_resolved):
        target.unlink(missing_ok=True)
        await ctx.reply(f"⚠️ 导出失败：解析后的路径 `{resolved}` 逃出了工作区")
        return

    try:
        rel = resolved.relative_to(workspace_resolved)
        path_display = f"`{rel}`"
    except ValueError:
        path_display = f"`{resolved}`"

    await ctx.reply(f"✓ 已导出到 {path_display}")


def _path_is_under(child: object, parent: object) -> bool:
    from pathlib import Path as _P

    child_p = _P(str(child))
    parent_p = _P(str(parent))
    return child_p == parent_p or parent_p in child_p.parents


def _truncate_utf8_bytes(s: str, max_bytes: int) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= max_bytes:
        return s
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated + "\n\n…（内容已截断）"


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
            args_padded = (args_part + " ").ljust(23) if args_part else "".ljust(23)
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
            name="/tier",
            handler=cmd_tier,
            category="config",
            help_text="查看或切换 permission tier (飞书:可切档;Web:仅显示,请用输入框 dropdown 切换)",
            args_hint="[read-only|approval|yolo]",
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
    registry.register(
        CommandSpec(
            name="/model",
            handler=cmd_model,
            category="model",
            help_text="查看/切换当前会话使用的 LLM 模型",
            args_hint="[name]",
            aliases=["/models"],
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )
    registry.register(
        CommandSpec(
            name="/compact",
            handler=cmd_compact,
            category="context",
            help_text="手动压缩会话上下文（60s 冷却）",
            args_hint="[focus]",
            channels=ALL_CHANNELS,
            requires_idle=True,
        )
    )
    registry.register(
        CommandSpec(
            name="/export",
            handler=cmd_export,
            category="context",
            help_text="导出会话到 markdown 或 json（可加 inline 直接回复）",
            args_hint="[markdown|json] [inline]",
            channels=ALL_CHANNELS,
            requires_idle=True,
        )
    )
    from pyclaw.core.commands.tasks import cmd_tasks

    registry.register(
        CommandSpec(
            name="/tasks",
            handler=cmd_tasks,
            category="inspection",
            help_text="查看/管理后台任务（当前会话 scope；--all 仅 admin）",
            args_hint="list [--all] | kill <id> [--confirm]",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    from pyclaw.core.commands.mcp import cmd_mcp

    registry.register(
        CommandSpec(
            name="/mcp",
            handler=cmd_mcp,
            category="config",
            help_text="MCP server 管理: list / restart <name> / logs <name>",
            args_hint="list | restart <name> | logs <name>",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    from pyclaw.core.commands.memory import cmd_memory

    registry.register(
        CommandSpec(
            name="/memory",
            handler=cmd_memory,
            category="inspection",
            help_text="查看当前会话记忆（L1/L2/L3/L4 分层；按 kind 过滤；stats 汇总）",
            args_hint="list [--facts|--procedures|--all] [--limit N] | search <q> | stats",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    from pyclaw.core.commands.curator import cmd_curator

    registry.register(
        CommandSpec(
            name="/curator",
            handler=cmd_curator,
            category="evolution",
            help_text="管理自演化 SOP（列表/晋升预览/恢复归档/手动 LLM review）",
            args_hint="list --auto|--stale|--archived | preview | restore <id> [--confirm] | review-status | review-trigger [--confirm]",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    from pyclaw.core.commands.skills import cmd_skills

    registry.register(
        CommandSpec(
            name="/skills",
            handler=cmd_skills,
            category="skills",
            help_text="管理本 workspace 的 skills（发现/搜索/安装/可用性检查）",
            args_hint="list | search <q> | install <slug> [--version V] [--confirm] | check [name]",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )

    from pyclaw.core.commands.readonly import (
        cmd_context,
        cmd_queue,
        cmd_resume,
        cmd_tools,
    )

    registry.register(
        CommandSpec(
            name="/tools",
            handler=cmd_tools,
            category="inspection",
            help_text="列出当前会话可用的工具（按副作用分组）",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )
    registry.register(
        CommandSpec(
            name="/queue",
            handler=cmd_queue,
            category="inspection",
            help_text="查看当前会话消息队列的位置 (pending + busy)",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )
    registry.register(
        CommandSpec(
            name="/context",
            handler=cmd_context,
            category="inspection",
            help_text="查看最近一次 run 的 token 使用量（input/output/cache）",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )
    registry.register(
        CommandSpec(
            name="/resume",
            handler=cmd_resume,
            category="session",
            help_text="切换到历史 session（支持 索引|后缀|current）",
            args_hint="[索引|后缀|current]",
            channels=ALL_CHANNELS,
            requires_idle=True,
        )
    )

    from pyclaw.core.commands.steering import cmd_btw, cmd_steer

    registry.register(
        CommandSpec(
            name="/steer",
            handler=cmd_steer,
            category="steering",
            help_text="在 agent 运行中注入指令（不打断当前 LLM call，下一轮生效）",
            args_hint="<message>",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )
    registry.register(
        CommandSpec(
            name="/btw",
            handler=cmd_btw,
            category="steering",
            help_text="在 agent 运行中插入侧问（软隔离，agent 简短作答后回到主任务）",
            args_hint="<question>",
            channels=ALL_CHANNELS,
            requires_idle=False,
        )
    )
