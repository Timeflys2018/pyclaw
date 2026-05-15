"""Tests for DbFileNamingPolicy (Phase A3).

Audit-trail anchors: A3.1 maps to tasks.md.

Contract:
    * ``HumanReadableNaming`` preserves 100% byte-level backward compatibility
      with the legacy ``session_key.replace(":", "_") + ".db"`` logic for all
      existing session_key formats (feishu/web/openai/test).
    * ``HashOnlyNaming`` produces deterministic hex filenames.
    * Degenerate inputs (empty / ``.`` / ``..``) do NOT produce path-traversal
      filenames; they fall back to a hash-salted ``_unsafe_*`` prefix.
"""

from __future__ import annotations

import hashlib
import logging

import pytest

from pyclaw.storage.memory.naming import (
    DbFileNamingPolicy,
    HashOnlyNaming,
    HumanReadableNaming,
)


class TestHumanReadableNamingBackwardCompat:
    """A3.1: legacy byte-level equality for the 5 canonical session_key formats."""

    @pytest.mark.parametrize(
        ("session_key", "expected"),
        [
            ("feishu:cli_abc:ou_xyz", "feishu_cli_abc_ou_xyz.db"),
            ("feishu:cli_abc:oc_grp:ou_usr", "feishu_cli_abc_oc_grp_ou_usr.db"),
            ("feishu:cli_abc:oc_grp:thread:t_th", "feishu_cli_abc_oc_grp_thread_t_th.db"),
            ("web:user123", "web_user123.db"),
            ("openai:user456", "openai_user456.db"),
        ],
    )
    def test_matches_legacy_replace_colon(
        self,
        session_key: str,
        expected: str,
    ) -> None:
        policy = HumanReadableNaming()
        assert policy.filename_for(session_key) == expected

    def test_dot_and_at_and_plus_are_preserved(self) -> None:
        policy = HumanReadableNaming()
        assert policy.filename_for("web:user.name") == "web_user.name.db"
        assert policy.filename_for("web:a@b.c") == "web_a@b.c.db"
        assert policy.filename_for("web:a+b") == "web_a+b.db"

    def test_unicode_preserved(self) -> None:
        policy = HumanReadableNaming()
        assert policy.filename_for("web:张三") == "web_张三.db"


class TestHumanReadableNamingSanitization:
    """A3.1: defense-in-depth Stage 2 replaces path separators + null + C0 controls."""

    def test_forward_slash_sanitized(self) -> None:
        policy = HumanReadableNaming()
        assert policy.filename_for("web:a/b") == "web_a_b.db"

    def test_backslash_sanitized(self) -> None:
        policy = HumanReadableNaming()
        assert policy.filename_for("web:a\\b") == "web_a_b.db"

    def test_null_byte_sanitized(self) -> None:
        policy = HumanReadableNaming()
        assert policy.filename_for("web:a\x00b") == "web_a_b.db"

    def test_newline_sanitized(self) -> None:
        policy = HumanReadableNaming()
        assert policy.filename_for("web:a\nb") == "web_a_b.db"

    def test_tab_sanitized(self) -> None:
        policy = HumanReadableNaming()
        assert policy.filename_for("web:a\tb") == "web_a_b.db"

    def test_path_traversal_neutralized(self) -> None:
        """``..`` within a key is preserved as bytes but the slashes around it
        are replaced — the resulting filename cannot escape the base dir."""
        policy = HumanReadableNaming()
        result = policy.filename_for("web:../etc/passwd")
        assert "/" not in result
        assert "\\" not in result
        assert result == "web_.._etc_passwd.db"

    def test_sanitization_emits_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        policy = HumanReadableNaming()
        with caplog.at_level(logging.WARNING, logger="pyclaw.storage.memory.naming"):
            policy.filename_for("web:a/b")
        assert any("dangerous chars" in rec.message.lower() for rec in caplog.records)


class TestHumanReadableNamingDegenerate:
    """A3.1: empty / '.' / '..' fall back to a hashed ``_unsafe_*`` prefix."""

    def test_empty_raises(self) -> None:
        policy = HumanReadableNaming()
        with pytest.raises(ValueError, match="empty"):
            policy.filename_for("")

    def test_dot_uses_unsafe_hash(self) -> None:
        policy = HumanReadableNaming()
        result = policy.filename_for(".")
        assert result.startswith("_unsafe_")
        assert result.endswith(".db")

    def test_double_dot_uses_unsafe_hash(self) -> None:
        policy = HumanReadableNaming()
        result = policy.filename_for("..")
        assert result.startswith("_unsafe_")
        assert result.endswith(".db")

    def test_whitespace_only_uses_unsafe_hash(self) -> None:
        policy = HumanReadableNaming()
        result = policy.filename_for("   ")
        assert result.startswith("_unsafe_")


class TestHashOnlyNaming:
    """A3.1: hash-based naming is deterministic and POSIX-safe."""

    def test_deterministic(self) -> None:
        policy = HashOnlyNaming()
        a = policy.filename_for("web:user1")
        b = policy.filename_for("web:user1")
        assert a == b

    def test_different_keys_produce_different_filenames(self) -> None:
        policy = HashOnlyNaming()
        a = policy.filename_for("web:user1")
        b = policy.filename_for("web:user2")
        assert a != b

    def test_output_is_hex_db(self) -> None:
        policy = HashOnlyNaming()
        result = policy.filename_for("web:user1")
        assert result.endswith(".db")
        stem = result[: -len(".db")]
        assert len(stem) == 64
        int(stem, 16)

    def test_matches_sha256_hexdigest(self) -> None:
        policy = HashOnlyNaming()
        session_key = "feishu:cli_abc:ou_xyz"
        expected = hashlib.sha256(session_key.encode()).hexdigest() + ".db"
        assert policy.filename_for(session_key) == expected

    def test_dangerous_input_still_produces_safe_filename(self) -> None:
        policy = HashOnlyNaming()
        result = policy.filename_for("../../etc/passwd")
        assert "/" not in result
        assert "\\" not in result
        assert result.endswith(".db")


class TestPolicyProtocol:
    """A3.1: both implementations satisfy the Protocol."""

    def test_human_readable_satisfies_protocol(self) -> None:
        policy: DbFileNamingPolicy = HumanReadableNaming()
        assert callable(policy.filename_for)

    def test_hash_only_satisfies_protocol(self) -> None:
        policy: DbFileNamingPolicy = HashOnlyNaming()
        assert callable(policy.filename_for)
