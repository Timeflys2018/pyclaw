"""Sprint 3 Phase 1 T1.5 — Per-user `tools_requiring_approval` REPLACE semantics.

4-slot review F2 fix.

Spec anchor: spec.md "Per-user tools_requiring_approval replace semantics"

Resolution rule:
- profile.tools_requiring_approval is None → use channel default (fall through)
- profile.tools_requiring_approval is [] → gate nothing (empty replaces default)
- profile.tools_requiring_approval is non-empty list → REPLACE channel default (not union, not intersect)
"""
from __future__ import annotations

import pytest

from pyclaw.auth.profile import UserProfile
from pyclaw.auth.tools_requiring_approval import resolve_tools_requiring_approval


class TestUserOverrideReplacesChannelDefault:
    def test_user_list_bash_only_does_not_gate_write(self) -> None:
        """alice has ['bash'] override; channel default ['bash','write','edit'].
        write_file should NOT be gated under alice's profile (REPLACE)."""
        profile = UserProfile(
            channel="web", user_id="alice", tools_requiring_approval=["bash"],
        )
        effective = resolve_tools_requiring_approval(
            profile=profile,
            channel_default=["bash", "write", "edit"],
        )
        assert effective == ["bash"]
        assert "write" not in effective
        assert "edit" not in effective

    def test_user_list_unrelated_tool_does_not_gate_anything_in_default(self) -> None:
        """alice override is ['custom_tool'] which isn't in channel default. REPLACE means
        ONLY 'custom_tool' is gated for alice; bash/write/edit are NOT gated for alice."""
        profile = UserProfile(
            channel="web", user_id="alice", tools_requiring_approval=["custom_tool"],
        )
        effective = resolve_tools_requiring_approval(
            profile=profile,
            channel_default=["bash", "write", "edit"],
        )
        assert effective == ["custom_tool"]
        assert "bash" not in effective


class TestNoneFallsThroughToChannelDefault:
    def test_user_none_uses_channel_default(self) -> None:
        profile = UserProfile(
            channel="web", user_id="alice", tools_requiring_approval=None,
        )
        effective = resolve_tools_requiring_approval(
            profile=profile,
            channel_default=["bash", "write", "edit"],
        )
        assert effective == ["bash", "write", "edit"]

    def test_no_profile_uses_channel_default(self) -> None:
        """When no profile is provided (legacy path), channel default applies."""
        effective = resolve_tools_requiring_approval(
            profile=None,
            channel_default=["bash", "write", "edit"],
        )
        assert effective == ["bash", "write", "edit"]


class TestEmptyListGatesNothing:
    def test_user_empty_list_overrides_default_to_gate_nothing(self) -> None:
        """alice has [] explicitly: that means 'gate nothing for me', overriding channel."""
        profile = UserProfile(
            channel="web", user_id="alice", tools_requiring_approval=[],
        )
        effective = resolve_tools_requiring_approval(
            profile=profile,
            channel_default=["bash", "write", "edit"],
        )
        assert effective == []
        assert "bash" not in effective


class TestEmptyChannelDefault:
    def test_empty_channel_default_with_no_user_override(self) -> None:
        profile = UserProfile(
            channel="web", user_id="alice", tools_requiring_approval=None,
        )
        effective = resolve_tools_requiring_approval(
            profile=profile,
            channel_default=[],
        )
        assert effective == []

    def test_user_override_can_add_gating_to_otherwise_empty_channel(self) -> None:
        profile = UserProfile(
            channel="web", user_id="alice", tools_requiring_approval=["bash"],
        )
        effective = resolve_tools_requiring_approval(
            profile=profile,
            channel_default=[],
        )
        assert effective == ["bash"]


@pytest.mark.parametrize(
    "user_override,channel_default,expected",
    [
        # REPLACE semantics
        (["bash"], ["bash", "write", "edit"], ["bash"]),
        (["a", "b"], ["c", "d"], ["a", "b"]),
        # None fall-through
        (None, ["bash", "write"], ["bash", "write"]),
        # Empty list explicit override
        ([], ["bash", "write"], []),
        # Both empty
        ([], [], []),
        (None, [], []),
    ],
)
def test_replace_table(user_override, channel_default, expected) -> None:
    profile = UserProfile(
        channel="web", user_id="alice", tools_requiring_approval=user_override,
    )
    assert (
        resolve_tools_requiring_approval(profile=profile, channel_default=channel_default)
        == expected
    )
