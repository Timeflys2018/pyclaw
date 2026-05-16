"""Sprint 3 Phase 3 — sandbox state resolver + PYCLAW_SANDBOX_OVERRIDE (4-slot F9)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from pyclaw.infra.settings import SandboxSettings
from pyclaw.sandbox.no_sandbox import NoSandboxPolicy
from pyclaw.sandbox.state import (
    OVERRIDE_ENV_VAR,
    SandboxStartupError,
    health_advisory,
    resolve_sandbox_state,
)


class TestPolicyNone:
    def test_returns_no_sandbox_policy(self) -> None:
        state = resolve_sandbox_state(SandboxSettings(policy="none"))
        assert isinstance(state.policy, NoSandboxPolicy)
        assert state.backend == "none"
        assert state.warning is None
        assert state.override_active is False


class TestPolicySrt:
    def test_srt_available_returns_srt_policy(self) -> None:
        from pyclaw.sandbox.srt import SrtPolicy

        with patch(
            "pyclaw.sandbox.srt.shutil.which",
            return_value="/opt/homebrew/bin/srt",
        ), patch("pyclaw.sandbox.state._detect_srt_version", return_value="1.0.0"):
            state = resolve_sandbox_state(SandboxSettings(policy="srt"))

        assert isinstance(state.policy, SrtPolicy)
        assert state.backend == "srt"
        assert state.srt_version == "1.0.0"
        assert state.warning is None

    def test_srt_missing_with_require_false_falls_back(self) -> None:
        with patch("pyclaw.sandbox.srt.shutil.which", return_value=None):
            state = resolve_sandbox_state(
                SandboxSettings(policy="srt", productionRequireSandbox=False)
            )
        assert isinstance(state.policy, NoSandboxPolicy)
        assert state.backend == "none"
        assert state.warning is not None and "srt not found" in state.warning

    def test_srt_missing_with_require_true_raises(self) -> None:
        with patch("pyclaw.sandbox.srt.shutil.which", return_value=None):
            with pytest.raises(SandboxStartupError):
                resolve_sandbox_state(
                    SandboxSettings(policy="srt", productionRequireSandbox=True)
                )


class TestPyclawSandboxOverrideEnv:
    """4-slot review F9 — emergency env override."""

    def test_disable_overrides_srt_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(OVERRIDE_ENV_VAR, "disable")
        with patch(
            "pyclaw.sandbox.srt.shutil.which",
            return_value="/opt/homebrew/bin/srt",
        ):
            state = resolve_sandbox_state(SandboxSettings(policy="srt"))

        assert isinstance(state.policy, NoSandboxPolicy)
        assert state.backend == "none"
        assert state.override_active is True
        assert state.warning is not None
        assert "PYCLAW_SANDBOX_OVERRIDE" in state.warning

    def test_disable_overrides_production_require(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(OVERRIDE_ENV_VAR, "disable")
        with patch("pyclaw.sandbox.srt.shutil.which", return_value=None):
            state = resolve_sandbox_state(
                SandboxSettings(policy="srt", productionRequireSandbox=True)
            )
        assert state.override_active is True
        assert state.backend == "none"

    def test_unset_or_other_value_does_not_override(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(OVERRIDE_ENV_VAR, raising=False)
        state = resolve_sandbox_state(SandboxSettings(policy="none"))
        assert state.override_active is False

        monkeypatch.setenv(OVERRIDE_ENV_VAR, "yes")
        state = resolve_sandbox_state(SandboxSettings(policy="none"))
        assert state.override_active is False


class TestHealthAdvisory:
    def test_payload_shape(self) -> None:
        state = resolve_sandbox_state(SandboxSettings(policy="none"))
        payload = health_advisory(state)
        assert payload["ready"] is True
        assert payload["backend"] == "none"
        assert payload["srt_version"] is None
        assert payload["warning"] is None
