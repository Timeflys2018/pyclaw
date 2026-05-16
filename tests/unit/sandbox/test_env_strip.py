"""Sprint 3 Phase 2 T2.3 — build_clean_env baseline behaviour.

Spec anchor: spec.md SandboxPolicy section + 4-slot review F10 hardcoded deny.

build_clean_env semantics (spec):
- Default allowlist: PATH, HOME, LANG, TERM
- Per-user/per-call allowlist extension passes additional explicit names through
- Hardcoded deny floor blocks ANTHROPIC_*/AWS_*/SSH_AUTH_SOCK/GITHUB_TOKEN/etc
  REGARDLESS of allowlist (4-slot review F10 fix)
"""
from __future__ import annotations

import pytest

from pyclaw.sandbox.env_strip import (
    DEFAULT_ALLOWLIST,
    HARDCODED_DENY_NAMES,
    build_clean_env,
    validate_env_allowlist,
)


class TestDefaultAllowlist:
    def test_passes_path_home_lang_term(self) -> None:
        parent = {
            "PATH": "/usr/bin:/bin",
            "HOME": "/Users/alice",
            "LANG": "en_US.UTF-8",
            "TERM": "xterm-256color",
            "FOO": "bar",
        }
        env = build_clean_env(allowlist=[], parent_env=parent)
        assert env["PATH"] == "/usr/bin:/bin"
        assert env["HOME"] == "/Users/alice"
        assert env["LANG"] == "en_US.UTF-8"
        assert env["TERM"] == "xterm-256color"
        assert "FOO" not in env

    def test_default_constants_documented(self) -> None:
        assert "PATH" in DEFAULT_ALLOWLIST
        assert "HOME" in DEFAULT_ALLOWLIST
        assert "LANG" in DEFAULT_ALLOWLIST
        assert "TERM" in DEFAULT_ALLOWLIST


class TestUserAllowlistExtension:
    def test_user_explicit_name_passes_through(self) -> None:
        parent = {"PATH": "/usr/bin", "MY_VAR": "value"}
        env = build_clean_env(allowlist=["MY_VAR"], parent_env=parent)
        assert env["MY_VAR"] == "value"

    def test_lc_all_preserved_when_in_allowlist(self) -> None:
        parent = {"PATH": "/usr/bin", "LC_ALL": "en_US.UTF-8"}
        env = build_clean_env(allowlist=["LC_ALL"], parent_env=parent)
        assert env["LC_ALL"] == "en_US.UTF-8"

    def test_unrelated_var_still_stripped(self) -> None:
        parent = {"PATH": "/usr/bin", "RANDOM_VAR": "x"}
        env = build_clean_env(allowlist=["LC_ALL"], parent_env=parent)
        assert "RANDOM_VAR" not in env


class TestHardcodedDenyFloor:
    """4-slot review F10 — these MUST never leak even if allowlist=['*']"""

    def test_anthropic_api_key_always_stripped(self) -> None:
        parent = {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-secret"}
        env = build_clean_env(allowlist=["*"], parent_env=parent)
        assert "ANTHROPIC_API_KEY" not in env

    def test_aws_credentials_always_stripped(self) -> None:
        parent = {
            "PATH": "/usr/bin",
            "AWS_ACCESS_KEY_ID": "AKIAxxx",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_SESSION_TOKEN": "token",
        }
        env = build_clean_env(allowlist=["*"], parent_env=parent)
        assert "AWS_ACCESS_KEY_ID" not in env
        assert "AWS_SECRET_ACCESS_KEY" not in env
        assert "AWS_SESSION_TOKEN" not in env

    def test_ssh_auth_sock_always_stripped(self) -> None:
        parent = {"PATH": "/usr/bin", "SSH_AUTH_SOCK": "/tmp/ssh-xxx/agent"}
        env = build_clean_env(allowlist=["*"], parent_env=parent)
        assert "SSH_AUTH_SOCK" not in env

    def test_github_token_always_stripped(self) -> None:
        parent = {"PATH": "/usr/bin", "GITHUB_TOKEN": "ghp_xxx", "GH_TOKEN": "y"}
        env = build_clean_env(allowlist=["*"], parent_env=parent)
        assert "GITHUB_TOKEN" not in env
        assert "GH_TOKEN" not in env

    def test_kubeconfig_always_stripped(self) -> None:
        parent = {"PATH": "/usr/bin", "KUBECONFIG": "/Users/alice/.kube/config"}
        env = build_clean_env(allowlist=["*"], parent_env=parent)
        assert "KUBECONFIG" not in env

    def test_pyclaw_internal_always_stripped(self) -> None:
        parent = {
            "PATH": "/usr/bin",
            "PYCLAW_SECRET": "s",
            "PYCLAW_LLM_API_KEY": "sk-x",
        }
        env = build_clean_env(allowlist=["*"], parent_env=parent)
        assert "PYCLAW_SECRET" not in env
        assert "PYCLAW_LLM_API_KEY" not in env

    def test_openai_api_key_always_stripped(self) -> None:
        parent = {"PATH": "/usr/bin", "OPENAI_API_KEY": "sk-y"}
        env = build_clean_env(allowlist=["*"], parent_env=parent)
        assert "OPENAI_API_KEY" not in env

    def test_litellm_always_stripped(self) -> None:
        parent = {"PATH": "/usr/bin", "LITELLM_PROXY": "p", "LITELLM_LOG": "DEBUG"}
        env = build_clean_env(allowlist=["*"], parent_env=parent)
        assert "LITELLM_PROXY" not in env
        assert "LITELLM_LOG" not in env

    def test_hardcoded_deny_names_documented(self) -> None:
        for name in (
            "ANTHROPIC_API_KEY",
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AWS_SESSION_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "SSH_AUTH_SOCK",
            "SSH_AGENT_PID",
            "KUBECONFIG",
            "KUBE_TOKEN",
            "OPENAI_API_KEY",
        ):
            assert name in HARDCODED_DENY_NAMES, f"{name} missing from deny floor"


class TestSpecificAllowlistVsHardcodedDeny:
    """4-slot review F10 — env_allowlist=['AWS_REGION'] passes AWS_REGION
    (specific name), but env_allowlist=['AWS_*'] is rejected at config load."""

    def test_aws_region_specific_allowlist_passes(self) -> None:
        parent = {
            "PATH": "/usr/bin",
            "AWS_REGION": "us-east-1",
            "AWS_ACCESS_KEY_ID": "AKIAxxx",
        }
        env = build_clean_env(allowlist=["AWS_REGION"], parent_env=parent)
        assert env["AWS_REGION"] == "us-east-1"
        assert "AWS_ACCESS_KEY_ID" not in env

    def test_validate_env_allowlist_rejects_aws_glob(self) -> None:
        with pytest.raises(ValueError, match="glob.*not allowed.*AWS_"):
            validate_env_allowlist(["AWS_*"])

    def test_validate_env_allowlist_rejects_anthropic_glob(self) -> None:
        with pytest.raises(ValueError, match="glob.*not allowed.*ANTHROPIC_"):
            validate_env_allowlist(["ANTHROPIC_*"])

    def test_validate_env_allowlist_accepts_safe_specific_names(self) -> None:
        validate_env_allowlist(["AWS_REGION", "LC_ALL", "LC_CTYPE"])

    def test_validate_env_allowlist_accepts_wildcard_alone(self) -> None:
        """`['*']` is permitted (means 'pass everything except hardcoded deny')."""
        validate_env_allowlist(["*"])


class TestWildcardAllowlist:
    def test_wildcard_passes_unrelated_vars(self) -> None:
        parent = {"PATH": "/usr/bin", "FOO": "bar", "BAZ": "qux"}
        env = build_clean_env(allowlist=["*"], parent_env=parent)
        assert env["FOO"] == "bar"
        assert env["BAZ"] == "qux"
