from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

from pyclaw.core.hooks import PermissionTier

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_VALID_TIERS: frozenset[str] = frozenset(("read-only", "approval", "yolo"))
_KEY_PREFIX = "pyclaw:feishu:tier"


def _redis_key(session_key: str) -> str:
    return f"{_KEY_PREFIX}:{session_key}"


async def get_session_tier(
    redis_client: "Redis | None",
    session_key: str,
) -> PermissionTier | None:
    """Read the per-session-key tier override stored in Redis.

    Returns ``None`` when redis is unavailable, the key is absent, or the
    stored value is invalid (caller falls back to deployment default).
    """
    if redis_client is None or not session_key:
        return None
    try:
        raw = await redis_client.get(_redis_key(session_key))
    except Exception:
        logger.warning("redis get_session_tier failed", exc_info=True)
        return None
    if raw is None:
        return None
    value = raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)
    if value in _VALID_TIERS:
        return value  # type: ignore[return-value]
    return None


async def set_session_tier(
    redis_client: "Redis | None",
    session_key: str,
    tier: PermissionTier,
    *,
    ttl_seconds: int = 7 * 24 * 3600,
) -> bool:
    """Persist the user's tier preference for ``session_key`` in Redis.

    Returns ``True`` on success. TTL defaults to 7 days mirroring the
    Redis session TTL (D-session-ttl): a user inactive for a week falls
    back to the deployment default on next interaction.
    """
    if redis_client is None or not session_key:
        return False
    if tier not in _VALID_TIERS:
        return False
    try:
        await redis_client.setex(_redis_key(session_key), ttl_seconds, tier)
        return True
    except Exception:
        logger.warning("redis set_session_tier failed", exc_info=True)
        return False


def parse_tier_arg(arg: str) -> PermissionTier | None:
    """Parse user-typed tier name. Accepts case-insensitive + common aliases.

    ``ro`` / ``read-only`` → ``read-only``
    ``ap`` / ``approval``  → ``approval``
    ``y`` / ``yolo``       → ``yolo``
    """
    s = arg.strip().lower()
    if s in ("read-only", "readonly", "ro", "read_only", "r"):
        return "read-only"
    if s in ("approval", "ap", "a"):
        return "approval"
    if s in ("yolo", "y"):
        return "yolo"
    return None


_TierLiteral = Literal["read-only", "approval", "yolo"]
