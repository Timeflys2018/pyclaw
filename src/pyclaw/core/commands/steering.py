from __future__ import annotations

from typing import Literal

from pyclaw.core.agent.run_control import RunControl, SteerMessage
from pyclaw.core.commands.context import CommandContext

CAP_COUNT = 5
CAP_CHARS = 2000


def _resolve_run_control(ctx: CommandContext) -> RunControl | None:
    provider = ctx.session_queue or ctx.queue_registry
    if provider is None or not hasattr(provider, "get_run_control"):
        return None
    if ctx.channel == "web":
        key = ctx.raw.get("conversation_id") or ctx.session_id
    else:
        key = ctx.session_id
    return provider.get_run_control(key)


def enforce_cap(rc: RunControl, new_text: str) -> tuple[bool, str]:
    if len(new_text) > CAP_CHARS:
        return (False, f"⚠ 单条消息超过 {CAP_CHARS} 字符，已拒绝")

    dropped = False
    while len(rc.pending_steers) >= CAP_COUNT:
        rc.pending_steers.pop(0)
        dropped = True

    while (
        sum(len(m.text) for m in rc.pending_steers) + len(new_text) > CAP_CHARS
        and rc.pending_steers
    ):
        rc.pending_steers.pop(0)
        dropped = True

    warning = "⚠ 已接收，但 buffer 满，丢弃最旧的 steer" if dropped else ""
    return (True, warning)


async def _handle(
    args: str,
    ctx: CommandContext,
    kind: Literal["steer", "sidebar"],
    command: str,
    arg_hint: str,
    accepted_msg: str,
) -> None:
    payload = args.strip()
    if not payload:
        await ctx.reply(f"⚠ {command} 需要参数：{command} {arg_hint}")
        return

    rc = _resolve_run_control(ctx)
    if rc is None:
        await ctx.reply("❌ 无法定位 session run control（channel queue unavailable）")
        return

    if not rc.is_active():
        await ctx.reply("⚠ 没有正在运行的 agent")
        return

    ok, warning = enforce_cap(rc, payload)
    if not ok:
        await ctx.reply(warning)
        return

    rc.pending_steers.append(SteerMessage(kind=kind, text=payload))

    reply = accepted_msg
    if warning:
        reply = f"{accepted_msg}\n{warning}"
    await ctx.reply(reply)


async def cmd_steer(args: str, ctx: CommandContext) -> None:
    await _handle(
        args,
        ctx,
        kind="steer",
        command="/steer",
        arg_hint="<message>",
        accepted_msg="✓ 已接收 steer 指令 (将在下一轮生效)",
    )


async def cmd_btw(args: str, ctx: CommandContext) -> None:
    await _handle(
        args,
        ctx,
        kind="sidebar",
        command="/btw",
        arg_hint="<question>",
        accepted_msg="✓ 已接收 side question (将在下一轮简短作答)",
    )
