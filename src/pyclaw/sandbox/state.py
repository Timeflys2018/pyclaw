from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any

from pyclaw.infra.settings import SandboxSettings
from pyclaw.sandbox.no_sandbox import NoSandboxPolicy
from pyclaw.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)

OVERRIDE_ENV_VAR = "PYCLAW_SANDBOX_OVERRIDE"
OVERRIDE_DISABLE_VALUE = "disable"


@dataclass
class SandboxState:
    policy: SandboxPolicy
    backend: str
    srt_version: str | None
    warning: str | None
    override_active: bool


def _detect_srt_version() -> str | None:
    path = shutil.which("srt")
    if path is None:
        return None
    try:
        import subprocess

        result = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip().splitlines()[0] if result.stdout else None
    except Exception:
        logger.warning("failed to detect srt --version", exc_info=True)
    return None


class SandboxStartupError(RuntimeError):
    """Raised when ``production_require_sandbox=true`` but srt missing."""


def resolve_sandbox_state(settings: SandboxSettings) -> SandboxState:
    """Resolve the runtime SandboxPolicy honoring config + env override.

    Honors ``PYCLAW_SANDBOX_OVERRIDE=disable`` (4-slot review F9) — when set,
    forces ``NoSandboxPolicy`` regardless of config and emits a CRITICAL warning.
    """
    override = os.environ.get(OVERRIDE_ENV_VAR, "").strip().lower()
    override_active = override == OVERRIDE_DISABLE_VALUE
    if override_active:
        logger.critical(
            "PYCLAW_SANDBOX_OVERRIDE=disable active; sandbox bypassed regardless of config"
        )
        return SandboxState(
            policy=NoSandboxPolicy(),
            backend="none",
            srt_version=None,
            warning="PYCLAW_SANDBOX_OVERRIDE=disable active; sandbox bypassed",
            override_active=True,
        )

    if settings.policy == "none":
        return SandboxState(
            policy=NoSandboxPolicy(),
            backend="none",
            srt_version=None,
            warning=None,
            override_active=False,
        )

    if settings.policy == "srt":
        from pyclaw.sandbox.srt import SrtBinaryNotFound, SrtPolicy

        srt_version = _detect_srt_version()
        try:
            policy = SrtPolicy(
                settings=settings,
                require_binary=settings.production_require_sandbox,
            )
        except SrtBinaryNotFound as exc:
            raise SandboxStartupError(str(exc)) from exc

        if policy.binary_path is None:
            warning = (
                "srt not found on PATH; sandbox falling back to NoSandboxPolicy. "
                "Install: npm install -g @anthropic-ai/sandbox-runtime"
            )
            logger.warning(warning)
            return SandboxState(
                policy=NoSandboxPolicy(),
                backend="none",
                srt_version=None,
                warning=warning,
                override_active=False,
            )

        return SandboxState(
            policy=policy,
            backend="srt",
            srt_version=srt_version,
            warning=None,
            override_active=False,
        )

    return SandboxState(
        policy=NoSandboxPolicy(),
        backend="none",
        srt_version=None,
        warning=f"unknown sandbox.policy={settings.policy!r}; falling back to none",
        override_active=False,
    )


def health_advisory(state: SandboxState) -> dict[str, Any]:
    return {
        "ready": True,
        "backend": state.backend,
        "srt_version": state.srt_version,
        "warning": state.warning,
    }
