from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Mapping, Protocol

from pyclaw.auth.profile import UserProfile

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

_KEY_PREFIX = "pyclaw:userprofile"
_DEFAULT_TTL_SECONDS = 30 * 24 * 3600


def _redis_key(channel: str, user_id: str) -> str:
    return f"{_KEY_PREFIX}:{channel}:{user_id}"


class UserProfileStore(Protocol):
    async def get(self, channel: str, user_id: str) -> UserProfile: ...

    async def set(
        self, profile: UserProfile, *, ttl_seconds: int | None = None
    ) -> bool: ...

    async def list_users(self, channel: str) -> list[UserProfile]: ...

    async def discard(self, channel: str, user_id: str) -> bool: ...


def _serialize(profile: UserProfile) -> str:
    return json.dumps(
        {
            "channel": profile.channel,
            "user_id": profile.user_id,
            "role": profile.role,
            "tier_default": profile.tier_default,
            "tools_requiring_approval": profile.tools_requiring_approval,
            "env_allowlist": profile.env_allowlist,
            "sandbox_overrides": profile.sandbox_overrides,
        },
        ensure_ascii=False,
    )


def _deserialize(raw: bytes | str) -> UserProfile | None:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        payload = json.loads(text)
    except Exception:
        logger.warning("UserProfile JSON decode failed", exc_info=True)
        return None
    if not isinstance(payload, dict):
        return None
    channel = payload.get("channel")
    user_id = payload.get("user_id")
    if channel not in ("web", "feishu") or not isinstance(user_id, str) or not user_id:
        return None
    role = payload.get("role", "member")
    if role not in ("admin", "member"):
        role = "member"
    tier_default = payload.get("tier_default")
    if tier_default not in (None, "read-only", "approval", "yolo"):
        tier_default = None
    return UserProfile(
        channel=channel,
        user_id=user_id,
        role=role,
        tier_default=tier_default,
        tools_requiring_approval=payload.get("tools_requiring_approval"),
        env_allowlist=payload.get("env_allowlist"),
        sandbox_overrides=payload.get("sandbox_overrides"),
    )


class RedisJsonStore:
    """UserProfileStore impl: Redis-first with JSON-file fallback.

    - ``get``: Redis hit → JSON fallback → default profile (role="member", tier_default=None).
    - ``set``: Redis SETEX 30 days (sliding window). JSON file is read-only at runtime.
    - ``list_users``: union of Redis SCAN matches + JSON entries; Redis takes precedence on overlap.
    - ``discard``: Redis DEL only (JSON is operator-controlled).
    """

    def __init__(
        self,
        *,
        redis_client: "Redis | None",
        json_source: Mapping[str, list[UserProfile]] | None = None,
    ) -> None:
        self._redis = redis_client
        self._json_source: Mapping[str, list[UserProfile]] = dict(json_source or {})

    async def get(self, channel: str, user_id: str) -> UserProfile:
        if self._redis is not None and user_id:
            try:
                raw = await self._redis.get(_redis_key(channel, user_id))
            except Exception:
                logger.warning(
                    "UserProfile redis get failed; falling back to JSON",
                    exc_info=True,
                )
                raw = None
            if raw is not None:
                profile = _deserialize(raw)
                if profile is not None:
                    return profile
        for entry in self._json_source.get(channel, ()):
            if entry.user_id == user_id:
                return entry
        return UserProfile(
            channel=channel,  # type: ignore[arg-type]
            user_id=user_id,
        )

    async def set(
        self, profile: UserProfile, *, ttl_seconds: int | None = None
    ) -> bool:
        if self._redis is None or not profile.user_id:
            return False
        ttl = _DEFAULT_TTL_SECONDS if ttl_seconds is None else ttl_seconds
        try:
            await self._redis.setex(
                _redis_key(profile.channel, profile.user_id),
                ttl,
                _serialize(profile),
            )
            return True
        except Exception:
            logger.warning("UserProfile redis setex failed", exc_info=True)
            return False

    async def list_users(self, channel: str) -> list[UserProfile]:
        seen: dict[str, UserProfile] = {}
        if self._redis is not None:
            pattern = f"{_KEY_PREFIX}:{channel}:*"
            try:
                async for key in self._redis.scan_iter(match=pattern):
                    key_str = key.decode("utf-8") if isinstance(key, bytes) else str(key)
                    try:
                        raw = await self._redis.get(key_str)
                    except Exception:
                        continue
                    if raw is None:
                        continue
                    profile = _deserialize(raw)
                    if profile is None or profile.channel != channel:
                        continue
                    seen[profile.user_id] = profile
            except Exception:
                logger.warning(
                    "UserProfile redis scan failed; using JSON only", exc_info=True
                )
        for entry in self._json_source.get(channel, ()):
            seen.setdefault(entry.user_id, entry)
        return list(seen.values())

    async def discard(self, channel: str, user_id: str) -> bool:
        if self._redis is None or not user_id:
            return False
        try:
            await self._redis.delete(_redis_key(channel, user_id))
            return True
        except Exception:
            logger.warning("UserProfile redis delete failed", exc_info=True)
            return False
