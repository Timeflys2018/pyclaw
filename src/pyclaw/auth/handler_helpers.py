from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pyclaw.auth.profile import UserProfile
from pyclaw.auth.profile_store import RedisJsonStore, UserProfileStore
from pyclaw.auth.tier_resolution import resolve_effective_tier
from pyclaw.core.hooks import PermissionTier

if TYPE_CHECKING:
    from redis.asyncio import Redis


def _build_default_store(
    *,
    channel: Literal["web", "feishu"],
    redis_client: "Redis | None",
    user_configs: list[Any],
    open_id_attr: str = "id",
) -> UserProfileStore:
    profiles: list[UserProfile] = []
    for cfg in user_configs or []:
        user_id = getattr(cfg, open_id_attr, None) or getattr(cfg, "open_id", None) or getattr(cfg, "id", None)
        if not user_id:
            continue
        profiles.append(
            UserProfile(
                channel=channel,
                user_id=str(user_id),
                role=getattr(cfg, "role", "member") or "member",
                tier_default=getattr(cfg, "tier_default", None),
                tools_requiring_approval=getattr(cfg, "tools_requiring_approval", None),
                env_allowlist=getattr(cfg, "env_allowlist", None),
                sandbox_overrides=getattr(cfg, "sandbox_overrides", None),
            )
        )
    json_source = {channel: profiles} if profiles else {}
    return RedisJsonStore(redis_client=redis_client, json_source=json_source)


async def resolve_profile_and_tier(
    *,
    channel: Literal["web", "feishu"],
    user_id: str,
    redis_client: "Redis | None",
    user_configs: list[Any],
    message_tier: PermissionTier | None,
    session_tier: PermissionTier | None,
    deployment_default: PermissionTier,
    store: UserProfileStore | None = None,
    open_id_attr: str = "id",
) -> tuple[UserProfile, PermissionTier]:
    """Look up the user's profile and resolve the effective per-turn tier.

    Returns ``(profile, effective_tier)``. ``effective_tier`` already applies
    Sprint 3 4-layer precedence (per-message > sessionKey > user > deployment).
    Channel handlers SHOULD pass the result to ``RunRequest.user_profile`` and
    ``RunRequest.permission_tier_override`` respectively.
    """
    resolved_store = store or _build_default_store(
        channel=channel,
        redis_client=redis_client,
        user_configs=user_configs,
        open_id_attr=open_id_attr,
    )
    profile = await resolved_store.get(channel, user_id)
    effective_tier = resolve_effective_tier(
        message_tier=message_tier,
        session_tier=session_tier,
        user_default=profile.tier_default,
        deployment_default=deployment_default,
    )
    return profile, effective_tier
