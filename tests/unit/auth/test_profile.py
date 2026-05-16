"""Sprint 3 Phase 1 T1.1 — UserProfile dataclass.

Spec anchor: openspec/changes/user-isolation-and-per-user-permissions/specs/tool-approval-tiers/spec.md
Requirement: "UserProfile schema and storage"

Validates:
- frozen dataclass (immutable)
- defaults: role="member", tier_default=None, tools_requiring_approval=None,
  env_allowlist=None, sandbox_overrides=None
- channel restricted to Literal["web", "feishu"] (Sprint 3 channels)
- channel + user_id are required
"""
from __future__ import annotations

import dataclasses

import pytest

from pyclaw.auth.profile import UserProfile


class TestUserProfileDefaults:
    def test_minimal_construction_has_member_role_default(self) -> None:
        profile = UserProfile(channel="web", user_id="alice")
        assert profile.role == "member"

    def test_tier_default_defaults_to_none(self) -> None:
        profile = UserProfile(channel="web", user_id="alice")
        assert profile.tier_default is None

    def test_tools_requiring_approval_defaults_to_none(self) -> None:
        profile = UserProfile(channel="web", user_id="alice")
        assert profile.tools_requiring_approval is None

    def test_env_allowlist_defaults_to_none(self) -> None:
        profile = UserProfile(channel="web", user_id="alice")
        assert profile.env_allowlist is None

    def test_sandbox_overrides_defaults_to_none(self) -> None:
        profile = UserProfile(channel="web", user_id="alice")
        assert profile.sandbox_overrides is None

    def test_feishu_channel_is_accepted(self) -> None:
        profile = UserProfile(channel="feishu", user_id="ou_xxx")
        assert profile.channel == "feishu"


class TestUserProfileFrozen:
    def test_role_is_immutable(self) -> None:
        profile = UserProfile(channel="web", user_id="alice")
        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.role = "admin"  # type: ignore[misc]

    def test_tier_default_is_immutable(self) -> None:
        profile = UserProfile(channel="web", user_id="alice", tier_default="yolo")
        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.tier_default = "read-only"  # type: ignore[misc]

    def test_user_id_is_immutable(self) -> None:
        profile = UserProfile(channel="web", user_id="alice")
        with pytest.raises(dataclasses.FrozenInstanceError):
            profile.user_id = "bob"  # type: ignore[misc]


class TestUserProfileExplicitFields:
    def test_admin_role_and_yolo_default_constructible(self) -> None:
        profile = UserProfile(
            channel="web",
            user_id="alice",
            role="admin",
            tier_default="yolo",
        )
        assert profile.role == "admin"
        assert profile.tier_default == "yolo"

    def test_read_only_default_with_custom_tools_list(self) -> None:
        profile = UserProfile(
            channel="web",
            user_id="bob",
            tier_default="read-only",
            tools_requiring_approval=["bash"],
        )
        assert profile.tier_default == "read-only"
        assert profile.tools_requiring_approval == ["bash"]

    def test_empty_tools_requiring_approval_list_is_distinct_from_none(self) -> None:
        """Empty list ([]) means "gate nothing"; None means "fall through to channel default".
        4-slot review F2 — REPLACE semantics requires distinguishing [] from None."""
        a = UserProfile(channel="web", user_id="a", tools_requiring_approval=[])
        b = UserProfile(channel="web", user_id="b", tools_requiring_approval=None)
        assert a.tools_requiring_approval == []
        assert b.tools_requiring_approval is None
        assert a.tools_requiring_approval != b.tools_requiring_approval
