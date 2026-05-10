from __future__ import annotations

import logging

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


async def cmd_model(args: str, ctx: CommandContext) -> None:
    from pyclaw.core.agent.llm import (
        LLMError,
        LLMErrorCode,
        resolve_provider_for_model,
    )
    from pyclaw.models import ModelChangeEntry, generate_entry_id, now_iso

    target = args.strip()
    available = list_available_models(ctx.agent_settings)

    if not target:
        tree = await ctx.deps.session_store.load(ctx.session_id)
        current = (
            tree.header.model_override if tree and tree.header.model_override else None
        ) or getattr(ctx.deps.llm, "default_model", "(unknown)")

        lines = [f"🤖 当前模型: `{current}`", ""]
        if available:
            lines.append("可用模型:")
            for provider, models in sorted(available.items()):
                lines.append(f"  📦 {provider}")
                for m in models:
                    lines.append(f"    • {m}")
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

    await ctx.reply(f"✓ 模型已切换为 `{target}`（下次对话生效）")


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
        await ctx.deps.session_store.append_entry(
            ctx.session_id, comp_entry, leaf_id=comp_entry.id
        )

    tokens_saved = max(0, result.tokens_before - (result.tokens_after or 0))
    await ctx.reply(f"✓ 压缩完成，节省约 {tokens_saved} tokens")


def _wrap_context_engine_with_focus(engine: object, focus: str) -> object:
    class _FocusedEngine:
        def __init__(self) -> None:
            self._inner = engine
            self._focus = focus

        def __getattr__(self, name: str) -> object:
            return getattr(self._inner, name)

        async def compact(self, **kwargs: object) -> object:
            messages = kwargs.get("messages") or []
            focus_msg = {
                "role": "system",
                "content": f"Compaction focus from user: {self._focus}",
            }
            kwargs["messages"] = [focus_msg, *messages]
            return await self._inner.compact(**kwargs)

    return _FocusedEngine()


def _failed_compact_result(error: str) -> object:
    from pyclaw.models import CompactResult

    return CompactResult(
        ok=False,
        compacted=False,
        reason=f"handler exception: {error}",
        reason_code="handler_error",
    )


async def cmd_export(args: str, ctx: CommandContext) -> None:
    import secrets
    from datetime import datetime, timezone
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
    utc_iso = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"session-{rand}-{utc_iso}.{ext}"
    target = exports_dir / filename

    target.write_text(rendered, encoding="utf-8")

    resolved = target.resolve()
    if not _path_is_under(resolved, workspace_resolved):
        target.unlink(missing_ok=True)
        await ctx.reply(
            f"⚠️ 导出失败：解析后的路径 `{resolved}` 逃出了工作区"
        )
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
