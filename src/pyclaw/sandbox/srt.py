from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import tempfile
import threading
from typing import Any, Literal

from pyclaw.infra.settings import SandboxSettings

logger = logging.getLogger(__name__)

ARG_MAX_FALLBACK_THRESHOLD = 100_000

_DEFAULT_DENY_DOMAINS: tuple[str, ...] = ("169.254.169.254",)


_GENERATED_FILES_LOCK = threading.Lock()
_GENERATED_FILES: set[str] = set()


def _cleanup_all_generated_files() -> None:
    with _GENERATED_FILES_LOCK:
        paths = list(_GENERATED_FILES)
        _GENERATED_FILES.clear()
    for path in paths:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except Exception:
            logger.debug("srt: cleanup of %s failed", path, exc_info=True)


atexit.register(_cleanup_all_generated_files)


class SrtBinaryNotFound(RuntimeError):
    """Raised when ``srt`` is required but missing on $PATH."""


class SrtCommandTooLong(RuntimeError):
    """Raised when a bash command exceeds ``ARG_MAX_FALLBACK_THRESHOLD``.

    4-slot review v2 A3 fix: previously the policy silently fell back to
    NoSandbox semantics, which an attacker could exploit by padding a
    payload to bypass isolation. Now refuses fail-closed.
    """


class SrtPolicy:
    """Sandbox policy backed by ``@anthropic-ai/sandbox-runtime`` (`srt` 1.0.0+).

    Generates a per-call ``srt-settings.json`` file under ``$TMPDIR`` with
    spike-S0.2 required schema fields populated:
    - ``filesystem.allowWrite`` / ``filesystem.denyRead`` / ``filesystem.denyWrite``
    - ``network.allowedDomains`` (specific domains, never ``"*"``)
    - ``network.deniedDomains`` (default includes IMDS protection ``169.254.169.254``)

    ``wrap_bash_command`` returns ``(srt_path, ["--settings", <cfg>, "/bin/sh", "-c", cmd])``.
    Long commands beyond ``ARG_MAX_FALLBACK_THRESHOLD`` fall back to NoSandbox
    semantics (``("/bin/sh", ["-c", cmd])``) and emit a warning.
    """

    backend: Literal["srt"] = "srt"

    def __init__(
        self,
        *,
        settings: SandboxSettings,
        require_binary: bool = False,
    ) -> None:
        self._settings = settings
        path = shutil.which("srt")
        if path is None and require_binary:
            raise SrtBinaryNotFound(
                "srt binary not found on $PATH; install via "
                "'npm install -g @anthropic-ai/sandbox-runtime' or "
                "'sandbox.production_require_sandbox=false' to disable enforcement"
            )
        self._binary_path = path
        self._tmpdir = tempfile.gettempdir()

    @property
    def binary_path(self) -> str | None:
        return self._binary_path

    def wrap_bash_command(
        self, cmd: str, ctx: Any
    ) -> tuple[str, list[str]]:
        if len(cmd) > ARG_MAX_FALLBACK_THRESHOLD:
            logger.error(
                "srt: command length %d exceeds threshold %d; refusing fail-closed "
                "(was silent NoSandbox bypass per 4-slot review v2 A3)",
                len(cmd),
                ARG_MAX_FALLBACK_THRESHOLD,
            )
            raise SrtCommandTooLong(
                f"sandbox: command length {len(cmd)} exceeds maximum "
                f"{ARG_MAX_FALLBACK_THRESHOLD} (security: refusing to fall back to "
                f"unsandboxed execution; shorten the command or use a script file)"
            )

        if self._binary_path is None:
            logger.warning("srt: binary missing at spawn; falling back to NoSandbox")
            return ("/bin/sh", ["-c", cmd])

        settings_path = self._generate_settings_json(ctx)
        return (
            self._binary_path,
            ["--settings", settings_path, "/bin/sh", "-c", cmd],
        )

    def wrap_mcp_stdio(
        self,
        params: Any,
        server_name: str,
        sandbox_config: Any,
    ) -> Any:
        if self._binary_path is None:
            logger.warning(
                "srt: binary missing; MCP server %s spawn will not be sandboxed",
                server_name,
            )
            return params

        settings_path = self._generate_settings_json(
            ctx=None, sandbox_config=sandbox_config, server_name=server_name
        )
        original_command = params.command
        original_args = list(params.args or [])

        try:
            params.command = self._binary_path
            params.args = ["--settings", settings_path, original_command, *original_args]
        except (AttributeError, TypeError):
            from mcp import StdioServerParameters

            return StdioServerParameters(
                command=self._binary_path,
                args=["--settings", settings_path, original_command, *original_args],
                env=getattr(params, "env", None),
            )
        return params

    def _generate_settings_json(
        self,
        ctx: Any = None,
        *,
        sandbox_config: Any = None,
        server_name: str | None = None,
    ) -> str:
        cfg = self._build_settings_dict(ctx=ctx, sandbox_config=sandbox_config)
        prefix = f"pyclaw-srt-{server_name or 'bash'}-"
        fd, path = tempfile.mkstemp(suffix=".json", prefix=prefix, dir=self._tmpdir)
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(cfg, f, ensure_ascii=False)
        except Exception:
            os.close(fd) if not f.closed else None  # type: ignore[has-type]
            raise
        with _GENERATED_FILES_LOCK:
            _GENERATED_FILES.add(path)
        return path

    @staticmethod
    def cleanup_settings_file(path: str) -> None:
        """Delete a generated srt-settings.json file. Idempotent.

        Callers (BashTool post-execution, MCP server stop) invoke this to
        avoid 4-slot review v2 F1 temp file accumulation. ``atexit`` also
        sweeps any leftovers at process shutdown.
        """
        with _GENERATED_FILES_LOCK:
            _GENERATED_FILES.discard(path)
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        except Exception:
            logger.debug("srt: cleanup of %s failed", path, exc_info=True)

    def _build_settings_dict(
        self, *, ctx: Any = None, sandbox_config: Any = None
    ) -> dict[str, Any]:
        fs_default = self._settings.default_filesystem
        net_default = self._settings.default_network

        allow_write = list(fs_default.allow_write)
        deny_read = list(fs_default.deny_read)
        deny_write = list(fs_default.deny_write)

        allowed_domains = [d for d in net_default.allowed_domains if d != "*"]
        denied_domains = list(net_default.denied_domains)
        for required in _DEFAULT_DENY_DOMAINS:
            if required not in denied_domains:
                denied_domains.append(required)

        env_allowlist = list(self._settings.default_env_allowlist)

        profile = getattr(ctx, "user_profile", None) if ctx is not None else None
        overrides = getattr(profile, "sandbox_overrides", None) if profile else None
        if isinstance(overrides, dict):
            fs_over = overrides.get("filesystem") or {}
            for entry in fs_over.get("allowWrite", []):
                if entry not in allow_write:
                    allow_write.append(entry)
            for entry in fs_over.get("denyRead", []):
                if entry not in deny_read:
                    deny_read.append(entry)
            for entry in fs_over.get("denyWrite", []):
                if entry not in deny_write:
                    deny_write.append(entry)
            net_over = overrides.get("network") or {}
            for entry in net_over.get("allowedDomains", []):
                if entry != "*" and entry not in allowed_domains:
                    allowed_domains.append(entry)
            for entry in net_over.get("deniedDomains", []):
                if entry not in denied_domains:
                    denied_domains.append(entry)

        if isinstance(sandbox_config, dict):
            fs_over = sandbox_config.get("filesystem") or {}
            for entry in fs_over.get("allowWrite", []):
                if entry not in allow_write:
                    allow_write.append(entry)
            net_over = sandbox_config.get("network") or {}
            for entry in net_over.get("allowedDomains", []):
                if entry != "*" and entry not in allowed_domains:
                    allowed_domains.append(entry)

        return {
            "filesystem": {
                "allowWrite": allow_write,
                "denyRead": deny_read,
                "denyWrite": deny_write,
            },
            "network": {
                "allowedDomains": allowed_domains,
                "deniedDomains": denied_domains,
            },
            "env": env_allowlist,
        }
