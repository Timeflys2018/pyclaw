from __future__ import annotations

import pytest

from pyclaw.integrations.mcp.settings import _substitute_env_placeholder


class TestEnvSubstitution:
    def test_resolved(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "ghp_abc123")
        resolved, status = _substitute_env_placeholder("{env:GITHUB_TOKEN}")
        assert resolved == "ghp_abc123"
        assert status == "resolved"

    def test_missing_env_var(self, monkeypatch):
        monkeypatch.delenv("NONEXISTENT_VAR_TEST", raising=False)
        resolved, status = _substitute_env_placeholder("{env:NONEXISTENT_VAR_TEST}")
        assert resolved is None
        assert status == "missing-env-var"

    def test_literal_pass_through(self):
        resolved, status = _substitute_env_placeholder("PYTHONUNBUFFERED=1")
        assert resolved == "PYTHONUNBUFFERED=1"
        assert status == "literal"

    def test_partial_placeholder_is_literal(self):
        value = "https://api.example.com/{env:VERSION}/v1"
        resolved, status = _substitute_env_placeholder(value)
        assert resolved == value
        assert status == "literal"

    def test_lowercase_var_name_is_literal(self):
        resolved, status = _substitute_env_placeholder("{env:http_proxy}")
        assert resolved == "{env:http_proxy}"
        assert status == "literal"

    def test_empty_var_name_rejected_as_literal(self):
        resolved, status = _substitute_env_placeholder("{env:}")
        assert resolved == "{env:}"
        assert status == "literal"

    def test_starts_with_digit_rejected_as_literal(self):
        resolved, status = _substitute_env_placeholder("{env:1FOO}")
        assert resolved == "{env:1FOO}"
        assert status == "literal"

    def test_var_with_underscore_prefix(self, monkeypatch):
        monkeypatch.setenv("_PRIVATE_VAR", "value")
        resolved, status = _substitute_env_placeholder("{env:_PRIVATE_VAR}")
        assert resolved == "value"
        assert status == "resolved"

    def test_trailing_whitespace_rejected_as_literal(self):
        resolved, status = _substitute_env_placeholder("{env:GITHUB_TOKEN} ")
        assert resolved == "{env:GITHUB_TOKEN} "
        assert status == "literal"
