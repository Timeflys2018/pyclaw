from __future__ import annotations

from typing import Any, Literal


class NoSandboxPolicy:
    """Passthrough sandbox policy — Sprint 3.0 backward-compat default.

    Preserves Sprint 2.0.1 byte-identical BashTool behavior. ``wrap_bash_command``
    returns ``("/bin/sh", ["-c", cmd])`` which matches
    ``asyncio.create_subprocess_shell`` semantics on POSIX.
    """

    backend: Literal["none"] = "none"

    def wrap_bash_command(self, cmd: str, ctx: Any) -> tuple[str, list[str]]:
        return ("/bin/sh", ["-c", cmd])

    def wrap_mcp_stdio(
        self,
        params: Any,
        server_name: str,
        sandbox_config: Any,
    ) -> Any:
        return params
