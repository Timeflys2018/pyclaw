"""Sprint 3 Phase 1 T1.2 — RedisJsonStore.

Spec anchor: spec.md "UserProfile schema and storage" + 3 scenarios:
- Redis-stored UserProfile takes precedence over JSON
- JSON fallback when Redis missing
- Default profile when neither Redis nor JSON has alice

Pattern reference: src/pyclaw/core/commands/tier_store.py +
tests/unit/core/commands/test_tier_store.py (Sprint 1 baseline).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from pyclaw.auth.profile import UserProfile
from pyclaw.auth.profile_store import RedisJsonStore


def _make_json_source(*profiles: UserProfile) -> dict[str, list[UserProfile]]:
    """Helper: simulate `settings.channels.{channel}.users[]` JSON fallback."""
    grouped: dict[str, list[UserProfile]] = {}
    for p in profiles:
        grouped.setdefault(p.channel, []).append(p)
    return grouped


class TestRedisHit:
    @pytest.mark.asyncio
    async def test_get_redis_hit_returns_redis_profile(self) -> None:
        """Redis takes precedence over JSON (sliding-window writes win)."""
        redis = AsyncMock()
        redis.get = AsyncMock(
            return_value=b'{"channel":"web","user_id":"alice","role":"admin","tier_default":"yolo"}'
        )
        json_source = _make_json_source(
            UserProfile(channel="web", user_id="alice", tier_default="read-only", role="member"),
        )
        store = RedisJsonStore(redis_client=redis, json_source=json_source)

        profile = await store.get("web", "alice")

        assert profile.role == "admin"
        assert profile.tier_default == "yolo"
        redis.get.assert_awaited_once_with("pyclaw:userprofile:web:alice")


class TestJsonFallback:
    @pytest.mark.asyncio
    async def test_get_redis_miss_returns_json_profile(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        json_source = _make_json_source(
            UserProfile(
                channel="web", user_id="alice", tier_default="read-only", role="member",
            ),
        )
        store = RedisJsonStore(redis_client=redis, json_source=json_source)

        profile = await store.get("web", "alice")

        assert profile.tier_default == "read-only"
        assert profile.role == "member"

    @pytest.mark.asyncio
    async def test_get_returns_default_profile_when_both_miss(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        store = RedisJsonStore(redis_client=redis, json_source={})

        profile = await store.get("web", "alice")

        assert profile.channel == "web"
        assert profile.user_id == "alice"
        assert profile.role == "member"
        assert profile.tier_default is None

    @pytest.mark.asyncio
    async def test_redis_unavailable_falls_back_to_json(self) -> None:
        """When redis_client is None, store should still serve JSON-stored profiles."""
        json_source = _make_json_source(
            UserProfile(channel="web", user_id="alice", role="admin"),
        )
        store = RedisJsonStore(redis_client=None, json_source=json_source)

        profile = await store.get("web", "alice")

        assert profile.role == "admin"

    @pytest.mark.asyncio
    async def test_redis_get_error_falls_back_to_json(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(side_effect=ConnectionError("redis down"))
        json_source = _make_json_source(
            UserProfile(channel="web", user_id="alice", role="admin"),
        )
        store = RedisJsonStore(redis_client=redis, json_source=json_source)

        profile = await store.get("web", "alice")

        assert profile.role == "admin"


class TestSetPersistsRedisWithTtl:
    @pytest.mark.asyncio
    async def test_set_writes_setex_30_days_default(self) -> None:
        redis = AsyncMock()
        redis.setex = AsyncMock()
        store = RedisJsonStore(redis_client=redis, json_source={})
        profile = UserProfile(
            channel="web", user_id="alice", role="admin", tier_default="yolo",
        )

        ok = await store.set(profile)

        assert ok is True
        redis.setex.assert_awaited_once()
        args = redis.setex.await_args
        assert args.args[0] == "pyclaw:userprofile:web:alice"
        assert args.args[1] == 30 * 24 * 3600  # 30 days
        # Third arg is JSON string; assert it round-trips
        import json as _json
        payload = _json.loads(args.args[2])
        assert payload["role"] == "admin"
        assert payload["tier_default"] == "yolo"
        assert payload["channel"] == "web"
        assert payload["user_id"] == "alice"

    @pytest.mark.asyncio
    async def test_set_returns_false_when_redis_none(self) -> None:
        store = RedisJsonStore(redis_client=None, json_source={})
        profile = UserProfile(channel="web", user_id="alice")
        assert await store.set(profile) is False

    @pytest.mark.asyncio
    async def test_set_swallows_redis_errors(self) -> None:
        redis = AsyncMock()
        redis.setex = AsyncMock(side_effect=ConnectionError("redis down"))
        store = RedisJsonStore(redis_client=redis, json_source={})
        profile = UserProfile(channel="web", user_id="alice")
        assert await store.set(profile) is False


class TestListUsers:
    @pytest.mark.asyncio
    async def test_list_users_returns_redis_plus_json_for_channel(self) -> None:
        redis = AsyncMock()
        # SCAN returns redis-stored alice
        redis.scan_iter = _make_scan_iter([b"pyclaw:userprofile:web:alice"])
        redis.get = AsyncMock(
            return_value=b'{"channel":"web","user_id":"alice","role":"admin","tier_default":"yolo"}'
        )
        # JSON has bob (only)
        json_source = _make_json_source(
            UserProfile(channel="web", user_id="bob", role="member"),
        )
        store = RedisJsonStore(redis_client=redis, json_source=json_source)

        users = await store.list_users("web")

        ids = sorted(u.user_id for u in users)
        assert ids == ["alice", "bob"]

    @pytest.mark.asyncio
    async def test_list_users_redis_takes_precedence_on_overlap(self) -> None:
        """If a user_id exists in both Redis and JSON, Redis version wins."""
        redis = AsyncMock()
        redis.scan_iter = _make_scan_iter([b"pyclaw:userprofile:web:alice"])
        redis.get = AsyncMock(
            return_value=b'{"channel":"web","user_id":"alice","role":"admin","tier_default":"yolo"}'
        )
        json_source = _make_json_source(
            UserProfile(channel="web", user_id="alice", role="member", tier_default="read-only"),
        )
        store = RedisJsonStore(redis_client=redis, json_source=json_source)

        users = await store.list_users("web")

        assert len(users) == 1
        assert users[0].role == "admin"
        assert users[0].tier_default == "yolo"

    @pytest.mark.asyncio
    async def test_list_users_filters_by_channel(self) -> None:
        redis = AsyncMock()
        redis.scan_iter = _make_scan_iter([])
        json_source = _make_json_source(
            UserProfile(channel="web", user_id="alice"),
            UserProfile(channel="feishu", user_id="ou_bob"),
        )
        store = RedisJsonStore(redis_client=redis, json_source=json_source)

        web_users = await store.list_users("web")
        feishu_users = await store.list_users("feishu")

        assert [u.user_id for u in web_users] == ["alice"]
        assert [u.user_id for u in feishu_users] == ["ou_bob"]


class TestDiscard:
    @pytest.mark.asyncio
    async def test_discard_removes_redis_key(self) -> None:
        redis = AsyncMock()
        redis.delete = AsyncMock(return_value=1)
        store = RedisJsonStore(redis_client=redis, json_source={})

        ok = await store.discard("web", "alice")

        assert ok is True
        redis.delete.assert_awaited_once_with("pyclaw:userprofile:web:alice")

    @pytest.mark.asyncio
    async def test_discard_returns_false_when_redis_none(self) -> None:
        store = RedisJsonStore(redis_client=None, json_source={})
        assert await store.discard("web", "alice") is False


def _make_scan_iter(keys: list[bytes]):
    """Helper: return an async iterator over keys for redis.scan_iter mocking."""
    async def _iter(match: str = "", count: int | None = None) -> Any:  # noqa: ARG001
        for k in keys:
            yield k
    return _iter
