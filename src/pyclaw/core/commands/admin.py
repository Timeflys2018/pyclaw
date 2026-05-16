"""/admin user slash command — Sprint 3 Phase 3.

Spec anchor: spec.md "/admin user slash command" Requirement + 4 scenarios
(including 4-slot review F4 last-admin-protection guard).
"""

from __future__ import annotations

import json
import logging
import shlex
from dataclasses import replace
from typing import Any

from pyclaw.auth.profile import UserProfile
from pyclaw.auth.profile_store import RedisJsonStore, UserProfileStore
from pyclaw.core.commands.context import CommandContext

logger = logging.getLogger(__name__)


_USAGE = (
    "用法: /admin user set <user_id> tier=<tier> [role=admin|member] | "
    "/admin user list | /admin user show <user_id> | "
    "/admin sandbox check"
)

_VALID_TIERS = frozenset({"read-only", "approval", "yolo"})
_VALID_ROLES = frozenset({"admin", "member"})


def _resolve_store(ctx: CommandContext) -> UserProfileStore | None:
    explicit = getattr(ctx, "user_profile_store", None)
    if explicit is not None:
        return explicit

    channel = ctx.channel
    settings = ctx.settings
    if channel == "web":
        users = list(settings.channels.web.users)
    elif channel == "feishu":
        users = list(settings.channels.feishu.users)
    else:
        users = []

    profiles: list[UserProfile] = []
    for cfg in users:
        user_id = (
            getattr(cfg, "id", None)
            or getattr(cfg, "open_id", None)
            or ""
        )
        if not user_id:
            continue
        profiles.append(
            UserProfile(
                channel=channel,  # type: ignore[arg-type]
                user_id=str(user_id),
                role=getattr(cfg, "role", "member") or "member",
                tier_default=getattr(cfg, "tier_default", None),
                tools_requiring_approval=getattr(cfg, "tools_requiring_approval", None),
                env_allowlist=getattr(cfg, "env_allowlist", None),
                sandbox_overrides=getattr(cfg, "sandbox_overrides", None),
            )
        )
    return RedisJsonStore(
        redis_client=getattr(ctx, "redis_client", None),
        json_source={channel: profiles} if profiles else {},
    )


def _is_admin(ctx: CommandContext) -> bool:
    raw = getattr(ctx, "raw", {}) or {}
    role = raw.get("user_role") if isinstance(raw, dict) else None
    if role == "admin":
        return True
    admin_ids = getattr(ctx, "admin_user_ids", None) or []
    return ctx.user_id in admin_ids


def _parse_kv_args(parts: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for token in parts:
        if "=" not in token:
            continue
        k, _, v = token.partition("=")
        out[k.strip().lower()] = v.strip()
    return out


async def cmd_admin(args: str, ctx: CommandContext) -> None:
    parts = shlex.split(args.strip()) if args.strip() else []
    if not parts:
        await ctx.reply(_USAGE)
        return

    domain = parts[0]
    if domain not in ("user", "sandbox"):
        await ctx.reply(f"❌ unknown /admin domain: {domain!r}\n{_USAGE}")
        return

    if not _is_admin(ctx):
        await ctx.reply("❌ Permission denied. /admin commands require admin role.")
        return

    sub_args = parts[1:]
    if not sub_args:
        await ctx.reply(_USAGE)
        return

    sub = sub_args[0]
    rest = sub_args[1:]

    if domain == "sandbox":
        if sub == "check":
            await _handle_sandbox_check(ctx)
        else:
            await ctx.reply(f"❌ unknown sandbox subcommand: {sub!r}\n{_USAGE}")
        return

    store = _resolve_store(ctx)
    if store is None:
        await ctx.reply("❌ UserProfileStore unavailable")
        return

    if sub == "list":
        await _handle_list(ctx, store, rest)
    elif sub == "show":
        await _handle_show(ctx, store, rest)
    elif sub == "set":
        await _handle_set(ctx, store, rest)
    else:
        await ctx.reply(f"❌ unknown subcommand: {sub!r}\n{_USAGE}")


async def _handle_list(
    ctx: CommandContext, store: UserProfileStore, rest: list[str]
) -> None:
    target_channel = ctx.channel
    for token in rest:
        if token.startswith("--channel="):
            target_channel = token.split("=", 1)[1]
        elif token == "--channel" and rest.index(token) + 1 < len(rest):
            target_channel = rest[rest.index(token) + 1]

    users = await store.list_users(target_channel)
    if not users:
        await ctx.reply(f"No user profiles configured for channel {target_channel!r}.")
        return

    lines = [f"User profiles for channel {target_channel!r}:"]
    for p in users:
        tier = p.tier_default or "—"
        lines.append(f"  {p.user_id:<24s}  role={p.role:<7s}  tier_default={tier}")
    await ctx.reply("\n".join(lines))


async def _handle_show(
    ctx: CommandContext, store: UserProfileStore, rest: list[str]
) -> None:
    if not rest:
        await ctx.reply("❌ usage: /admin user show <user_id>")
        return
    target = rest[0]
    profile = await store.get(ctx.channel, target)
    payload = {
        "channel": profile.channel,
        "user_id": profile.user_id,
        "role": profile.role,
        "tier_default": profile.tier_default,
        "tools_requiring_approval": profile.tools_requiring_approval,
        "env_allowlist": profile.env_allowlist,
        "sandbox_overrides": profile.sandbox_overrides,
    }
    await ctx.reply(f"```json\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n```")


async def _handle_set(
    ctx: CommandContext, store: UserProfileStore, rest: list[str]
) -> None:
    if not rest:
        await ctx.reply(
            "❌ usage: /admin user set <user_id> tier=<tier> [role=admin|member]"
        )
        return
    target = rest[0]
    kvs = _parse_kv_args(rest[1:])

    new_tier = kvs.get("tier")
    if new_tier is not None and new_tier not in _VALID_TIERS:
        await ctx.reply(
            f"❌ invalid tier {new_tier!r}; expected one of {sorted(_VALID_TIERS)}"
        )
        return
    new_role = kvs.get("role")
    if new_role is not None and new_role not in _VALID_ROLES:
        await ctx.reply(
            f"❌ invalid role {new_role!r}; expected one of {sorted(_VALID_ROLES)}"
        )
        return

    current = await store.get(ctx.channel, target)
    target_role = new_role if new_role is not None else current.role

    if (
        target == ctx.user_id
        and current.role == "admin"
        and target_role != "admin"
    ):
        admins = await store.list_users(ctx.channel, role_filter="admin")
        admin_count = len({u.user_id for u in admins})
        if admin_count <= 1:
            await ctx.reply(
                "❌ Cannot demote the last admin. "
                "Promote another user to admin first."
            )
            logger.info(
                "last-admin-protection: refused self-demote for %s in %s",
                ctx.user_id,
                ctx.channel,
            )
            return

    updated = replace(
        current,
        role=target_role,  # type: ignore[arg-type]
        tier_default=new_tier if new_tier is not None else current.tier_default,
    )
    ok = await store.set(updated)
    if ok:
        await ctx.reply(f"✅ User profile updated for {target}")
    else:
        await ctx.reply(
            f"⚠️ Failed to persist profile for {target} (Redis unavailable?)"
        )


async def _handle_sandbox_check(ctx: CommandContext) -> None:
    from pyclaw.integrations.mcp.settings import _command_auto_exempts_sandbox

    mcp_manager = getattr(ctx, "mcp_manager", None)
    settings = getattr(ctx, "settings", None)
    raw = getattr(ctx, "raw", {}) or {}
    sandbox_state = raw.get("sandbox_state") if isinstance(raw, dict) else None

    lines: list[str] = ["Sandbox check:"]

    if sandbox_state is not None:
        backend = getattr(sandbox_state, "backend", "?")
        srt_version = getattr(sandbox_state, "srt_version", None) or "—"
        warning = getattr(sandbox_state, "warning", None)
        override_active = getattr(sandbox_state, "override_active", False)
        lines.append(f"  backend={backend}  srt_version={srt_version}")
        if override_active:
            lines.append(f"  ⚠️ PYCLAW_SANDBOX_OVERRIDE active")
        if warning:
            lines.append(f"  warning: {warning}")
    else:
        lines.append("  (sandbox state unavailable)")

    if mcp_manager is None or settings is None:
        lines.append("  MCP disabled or settings unavailable.")
        await ctx.reply("\n".join(lines))
        return

    mcp_settings = settings.mcp
    if not mcp_settings.servers:
        lines.append("  No MCP servers configured.")
        await ctx.reply("\n".join(lines))
        return

    lines.append("")
    lines.append("MCP servers:")
    for name, server in mcp_settings.servers.items():
        sandbox_enabled = bool(server.sandbox.enabled)
        auto_exempt = _command_auto_exempts_sandbox(server.command)
        misconfig: list[str] = []

        if (
            auto_exempt
            and sandbox_enabled
            and "registry.npmjs.org" not in (server.sandbox.network or {}).get(
                "allowedDomains", []
            )
            and "registry.npmmirror.com" not in (server.sandbox.network or {}).get(
                "allowedDomains", []
            )
        ):
            misconfig.append(
                "command=npx/uvx + sandbox.enabled=true but no npm registry "
                "domain in allowedDomains; consider local binary path or add "
                "'registry.npmjs.org' to allowedDomains"
            )

        status = mcp_manager._servers.get(name) if hasattr(mcp_manager, "_servers") else None
        status_str = getattr(status, "status", "?") if status else "?"
        lines.append(
            f"  {name:<20s}  command={server.command!r:<30s}  "
            f"sandbox={sandbox_enabled}  status={status_str}"
        )
        for warn in misconfig:
            lines.append(f"    ⚠️ {warn}")

    await ctx.reply("\n".join(lines))


__all__ = ["cmd_admin"]
