from __future__ import annotations

from pyclaw.core.hooks import PermissionTier


def resolve_effective_tier(
    *,
    message_tier: PermissionTier | None,
    session_tier: PermissionTier | None,
    user_default: PermissionTier | None,
    deployment_default: PermissionTier,
) -> PermissionTier:
    """Resolve the effective per-turn tier per Sprint 3 4-layer precedence.

    Order (highest to lowest, first non-None wins):
    1. ``message_tier`` — per-message override from the channel handler
    2. ``session_tier`` — per-sessionKey override from Redis
    3. ``user_default`` — per-user ``UserProfile.tier_default``
    4. ``deployment_default`` — channel deployment default (always non-None)
    """
    if message_tier is not None:
        return message_tier
    if session_tier is not None:
        return session_tier
    if user_default is not None:
        return user_default
    return deployment_default
