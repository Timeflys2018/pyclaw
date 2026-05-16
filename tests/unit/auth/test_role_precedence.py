"""Sprint 3 Phase 1 T1.3 — 4-layer tier resolution precedence.

Spec anchor: spec.md "Per-user tier default as the third precedence layer"

Precedence (highest to lowest):
1. Per-message override (msg.tier_override)
2. Per-sessionKey override (Redis pyclaw:feishu:tier:{sessionKey})
3. Per-user UserProfile.tier_default
4. Channel deployment default (settings.default_permission_tier)

The first non-None value wins.
"""
from __future__ import annotations

import pytest

from pyclaw.auth.tier_resolution import resolve_effective_tier


class TestPrecedenceLayer1MessageWins:
    def test_per_message_beats_session(self) -> None:
        """msg='yolo' + session='approval' → yolo"""
        result = resolve_effective_tier(
            message_tier="yolo",
            session_tier="approval",
            user_default="read-only",
            deployment_default="approval",
        )
        assert result == "yolo"

    def test_per_message_beats_user(self) -> None:
        result = resolve_effective_tier(
            message_tier="approval",
            session_tier=None,
            user_default="yolo",
            deployment_default="read-only",
        )
        assert result == "approval"

    def test_per_message_beats_deployment(self) -> None:
        result = resolve_effective_tier(
            message_tier="read-only",
            session_tier=None,
            user_default=None,
            deployment_default="yolo",
        )
        assert result == "read-only"


class TestPrecedenceLayer2SessionWins:
    def test_session_beats_user(self) -> None:
        result = resolve_effective_tier(
            message_tier=None,
            session_tier="yolo",
            user_default="read-only",
            deployment_default="approval",
        )
        assert result == "yolo"

    def test_session_beats_deployment(self) -> None:
        result = resolve_effective_tier(
            message_tier=None,
            session_tier="read-only",
            user_default=None,
            deployment_default="yolo",
        )
        assert result == "read-only"


class TestPrecedenceLayer3UserWins:
    def test_user_beats_deployment(self) -> None:
        """user='yolo' + deployment='approval', no msg/session → yolo"""
        result = resolve_effective_tier(
            message_tier=None,
            session_tier=None,
            user_default="yolo",
            deployment_default="approval",
        )
        assert result == "yolo"

    def test_user_read_only_beats_deployment_approval(self) -> None:
        """alice: tier_default=read-only → effective=read-only (matches spec scenario)"""
        result = resolve_effective_tier(
            message_tier=None,
            session_tier=None,
            user_default="read-only",
            deployment_default="approval",
        )
        assert result == "read-only"


class TestPrecedenceLayer4DeploymentFallback:
    def test_all_none_falls_through_to_deployment(self) -> None:
        result = resolve_effective_tier(
            message_tier=None,
            session_tier=None,
            user_default=None,
            deployment_default="approval",
        )
        assert result == "approval"

    def test_deployment_yolo_used_when_others_none(self) -> None:
        result = resolve_effective_tier(
            message_tier=None,
            session_tier=None,
            user_default=None,
            deployment_default="yolo",
        )
        assert result == "yolo"


class TestNoneSkipping:
    """Each layer being None must skip to the next; non-None first wins."""

    def test_message_none_uses_session(self) -> None:
        assert (
            resolve_effective_tier(
                message_tier=None,
                session_tier="yolo",
                user_default="read-only",
                deployment_default="approval",
            )
            == "yolo"
        )

    def test_message_and_session_none_uses_user(self) -> None:
        assert (
            resolve_effective_tier(
                message_tier=None,
                session_tier=None,
                user_default="read-only",
                deployment_default="approval",
            )
            == "read-only"
        )

    def test_message_session_user_none_uses_deployment(self) -> None:
        assert (
            resolve_effective_tier(
                message_tier=None,
                session_tier=None,
                user_default=None,
                deployment_default="approval",
            )
            == "approval"
        )


@pytest.mark.parametrize(
    "msg,sess,user,dep,expected",
    [
        # Layer 1 wins regardless of others
        ("yolo", "approval", "read-only", "approval", "yolo"),
        ("approval", "yolo", "read-only", "read-only", "approval"),
        ("read-only", "yolo", "yolo", "yolo", "read-only"),
        # Layer 2 wins when 1=None
        (None, "yolo", "read-only", "approval", "yolo"),
        (None, "approval", "yolo", "read-only", "approval"),
        (None, "read-only", "yolo", "yolo", "read-only"),
        # Layer 3 wins when 1,2=None
        (None, None, "yolo", "approval", "yolo"),
        (None, None, "approval", "yolo", "approval"),
        (None, None, "read-only", "yolo", "read-only"),
        # Layer 4 fallback
        (None, None, None, "yolo", "yolo"),
        (None, None, None, "approval", "approval"),
        (None, None, None, "read-only", "read-only"),
        # Mixed: gap in middle still falls through
        (None, None, "read-only", "yolo", "read-only"),
        ("yolo", None, None, "approval", "yolo"),
        (None, "approval", None, "yolo", "approval"),
    ],
)
def test_precedence_table_combinations(msg, sess, user, dep, expected) -> None:
    """16+ combinations covering all layer x tier permutations."""
    assert (
        resolve_effective_tier(
            message_tier=msg,
            session_tier=sess,
            user_default=user,
            deployment_default=dep,
        )
        == expected
    )
