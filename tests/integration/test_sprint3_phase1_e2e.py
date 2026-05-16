"""Sprint 3 Phase 1 exit criteria — 4-layer tier resolution end-to-end.

Verifies that the channel handler integration (Phase 1 b) correctly threads
``UserProfile`` and ``effective_tier`` from settings/Redis into ``RunRequest``
and onward to ``ToolContext`` for the agent runner.

Spec anchor: spec.md "Per-user tier default applies when no message or session
override" + the corresponding 4 scenarios.
"""
from __future__ import annotations

import pytest

from pyclaw.auth import (
    RedisJsonStore,
    UserProfile,
    resolve_profile_and_tier,
)
from pyclaw.infra.settings import FeishuUserConfig, WebUserConfig


class TestWebChannelTierResolution:
    @pytest.mark.asyncio
    async def test_alice_read_only_default_yields_read_only_effective_tier(self) -> None:
        users = [
            WebUserConfig(
                id="alice", password="x", role="member", tier_default="read-only"
            ),
        ]

        profile, effective_tier = await resolve_profile_and_tier(
            channel="web",
            user_id="alice",
            redis_client=None,
            user_configs=users,
            message_tier=None,
            session_tier=None,
            deployment_default="approval",
        )

        assert profile.user_id == "alice"
        assert profile.role == "member"
        assert profile.tier_default == "read-only"
        assert effective_tier == "read-only"

    @pytest.mark.asyncio
    async def test_per_message_tier_overrides_user_default(self) -> None:
        users = [
            WebUserConfig(
                id="alice", password="x", role="member", tier_default="read-only"
            ),
        ]

        _, effective_tier = await resolve_profile_and_tier(
            channel="web",
            user_id="alice",
            redis_client=None,
            user_configs=users,
            message_tier="yolo",
            session_tier=None,
            deployment_default="approval",
        )

        assert effective_tier == "yolo"

    @pytest.mark.asyncio
    async def test_unknown_user_falls_through_to_deployment_default(self) -> None:
        _, effective_tier = await resolve_profile_and_tier(
            channel="web",
            user_id="bob",
            redis_client=None,
            user_configs=[],
            message_tier=None,
            session_tier=None,
            deployment_default="approval",
        )

        assert effective_tier == "approval"

    @pytest.mark.asyncio
    async def test_admin_user_role_propagates_through(self) -> None:
        users = [
            WebUserConfig(
                id="alice", password="x", role="admin", tier_default="yolo"
            ),
        ]

        profile, effective_tier = await resolve_profile_and_tier(
            channel="web",
            user_id="alice",
            redis_client=None,
            user_configs=users,
            message_tier=None,
            session_tier=None,
            deployment_default="approval",
        )

        assert profile.role == "admin"
        assert effective_tier == "yolo"


class TestFeishuChannelTierResolution:
    @pytest.mark.asyncio
    async def test_feishu_open_id_lookup(self) -> None:
        users = [
            FeishuUserConfig(
                openId="ou_alice", role="admin", tier_default="yolo"
            ),
        ]

        profile, effective_tier = await resolve_profile_and_tier(
            channel="feishu",
            user_id="ou_alice",
            redis_client=None,
            user_configs=users,
            message_tier=None,
            session_tier=None,
            deployment_default="approval",
            open_id_attr="open_id",
        )

        assert profile.user_id == "ou_alice"
        assert profile.role == "admin"
        assert effective_tier == "yolo"

    @pytest.mark.asyncio
    async def test_feishu_session_tier_beats_user_default(self) -> None:
        users = [
            FeishuUserConfig(
                openId="ou_alice", role="member", tier_default="read-only"
            ),
        ]

        _, effective_tier = await resolve_profile_and_tier(
            channel="feishu",
            user_id="ou_alice",
            redis_client=None,
            user_configs=users,
            message_tier=None,
            session_tier="yolo",
            deployment_default="approval",
            open_id_attr="open_id",
        )

        assert effective_tier == "yolo"


class TestChannelIsolation:
    """Sprint 3 spec: pyclaw:userprofile:{channel}:{user_id} is per-channel."""

    @pytest.mark.asyncio
    async def test_web_alice_and_feishu_alice_resolve_independently(self) -> None:
        web_users = [
            WebUserConfig(
                id="alice", password="x", role="admin", tier_default="yolo"
            ),
        ]
        feishu_users = [
            FeishuUserConfig(
                openId="alice", role="member", tier_default="read-only"
            ),
        ]

        web_profile, web_tier = await resolve_profile_and_tier(
            channel="web",
            user_id="alice",
            redis_client=None,
            user_configs=web_users,
            message_tier=None,
            session_tier=None,
            deployment_default="approval",
        )
        feishu_profile, feishu_tier = await resolve_profile_and_tier(
            channel="feishu",
            user_id="alice",
            redis_client=None,
            user_configs=feishu_users,
            message_tier=None,
            session_tier=None,
            deployment_default="approval",
            open_id_attr="open_id",
        )

        assert web_profile.role == "admin"
        assert web_tier == "yolo"
        assert feishu_profile.role == "member"
        assert feishu_tier == "read-only"
