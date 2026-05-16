"""Pydantic settings for the MCP integration.

Loaded as part of the top-level :class:`pyclaw.infra.settings.Settings`
(under the ``mcp`` field). Defaults to disabled so existing deployments
without MCP configured see no behavior change.
"""

from __future__ import annotations

import os
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


_ENV_PLACEHOLDER_RE = re.compile(r"^\{env:([A-Z_][A-Z0-9_]*)\}$")
_FORBIDDEN_KEY_CHARS = (":", "__")

_AUTO_EXEMPT_BASENAMES: frozenset[str] = frozenset({"npx", "uvx"})


PermissionTier = Literal["read-only", "approval", "yolo"]


def _command_auto_exempts_sandbox(command: str) -> bool:
    """Whether ``command``'s basename matches the npx/uvx auto-exempt list.

    Path semantics: only the final basename is compared (so
    ``/usr/local/bin/npx`` matches but ``/Users/alice/npx-tools/bin/server``
    does not). 4-slot review F1 fix.
    """
    if not command:
        return False
    last = command.rstrip("/").rsplit("/", 1)[-1]
    return last in _AUTO_EXEMPT_BASENAMES


class McpSandboxConfig(BaseModel):
    """Per-server sandbox config. Default ``enabled`` is conditional on command.

    See ``McpServerConfig.model_validator`` for the conditional-default rule:
    npx/uvx → ``enabled=False`` (4-slot F1 auto-exempt); else
    ``enabled=True``. Operators can always force either value explicitly.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool | None = Field(
        default=None,
        description=(
            "When None, the McpServerConfig validator picks a default based on "
            "the command basename (npx/uvx → False, else True). Set explicitly "
            "to override."
        ),
    )
    filesystem: dict = Field(default_factory=dict)
    network: dict = Field(default_factory=dict)
    env_allowlist: list[str] | None = Field(default=None)


def _substitute_env_placeholder(value: str) -> tuple[str | None, str]:
    """Resolve a single ``env`` dict value.

    Returns a ``(resolved, status)`` pair where ``status`` is one of:

    * ``"resolved"`` — value matched the placeholder regex AND the env var
      is set; ``resolved`` is the env var's value.
    * ``"literal"`` — value did not match the placeholder regex; ``resolved``
      is the original value verbatim. Partial matches like
      ``"prefix-{env:VAR}"`` and bare strings both fall here (we explicitly
      do NOT do partial substitution — too magical, error-prone).
    * ``"missing-env-var"`` — value matched the placeholder regex but the
      env var is not set in :data:`os.environ`; ``resolved`` is ``None`` so
      the caller can mark the server ``failed`` with the missing var name.

    The regex requires the var name to be uppercase / underscore / digit and
    start with a letter or underscore. Lowercase env vars (like
    ``http_proxy``) intentionally fall to ``"literal"`` rather than raising;
    operators wanting them must use a wrapper script. Documented behavior.
    """

    match = _ENV_PLACEHOLDER_RE.match(value)
    if match is None:
        return value, "literal"
    var_name = match.group(1)
    env_value = os.environ.get(var_name)
    if env_value is None:
        return None, "missing-env-var"
    return env_value, "resolved"


class McpServerConfig(BaseModel):
    """Per-server MCP configuration.

    Field types and defaults mirror the ``McpServerConfig`` table in the
    ``mcp-integration`` capability spec.
    """

    model_config = ConfigDict(extra="forbid")

    command: str = Field(..., description="Executable to spawn (e.g. 'npx', 'uvx').")
    args: list[str] = Field(default_factory=list, description="Arguments passed to command.")
    env: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Environment variables for the spawned process. Values may use "
            "{env:VAR_NAME} placeholders, resolved at server-startup time."
        ),
    )
    transport: Literal["stdio"] = Field(
        default="stdio",
        description="Transport type. Sprint 2 supports stdio only.",
    )
    enabled: bool = Field(default=True, description="Per-server kill switch.")
    trust_annotations: bool = Field(
        default=True,
        description=(
            "When True, derive tool_class from MCP ToolAnnotations.readOnlyHint. "
            "When False, force tool_class='write' for safety."
        ),
    )
    forced_tool_class: Literal["read", "write"] | None = Field(
        default=None,
        description="Override the derived tool_class for every tool from this server.",
    )
    forced_tier: PermissionTier | None = Field(
        default=None,
        description=(
            "Per-server permission tier. Per the de-escalation-only contract, "
            "forced_tier only takes effect when strictly more restrictive than "
            "the user's per-turn tier. forced_tier='yolo' is therefore a no-op."
        ),
    )
    connect_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        description="Maximum seconds to wait for server connection during startup.",
    )
    call_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Maximum seconds to wait for a single tool invocation.",
    )
    sandbox: McpSandboxConfig = Field(
        default_factory=McpSandboxConfig,
        description=(
            "Per-server sandbox configuration. When ``sandbox.enabled`` is "
            "None (default), it resolves to False for npx/uvx commands and "
            "True otherwise (4-slot review F1)."
        ),
    )

    @model_validator(mode="after")
    def _resolve_sandbox_default(self) -> "McpServerConfig":
        if self.sandbox.enabled is None:
            auto_exempt = _command_auto_exempts_sandbox(self.command)
            object.__setattr__(self.sandbox, "enabled", not auto_exempt)
        return self


class McpSettings(BaseModel):
    """Top-level MCP settings, loaded as ``Settings.mcp``.

    Validators reject server config keys containing ``:`` or ``__`` because
    those characters are reserved for the canonical ``{server}:{tool}``
    namespace and the LLM-API ``__`` rewrite respectively. See spec
    ``tool-registry`` requirement "Name validation enforces unambiguous
    round-trip".
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Master switch. When False, no MCP servers connect and no MCP code paths run.",
    )
    servers: dict[str, McpServerConfig] = Field(default_factory=dict)

    @field_validator("servers")
    @classmethod
    def _validate_server_keys(cls, servers: dict[str, McpServerConfig]) -> dict[str, McpServerConfig]:
        for key in servers:
            for forbidden in _FORBIDDEN_KEY_CHARS:
                if forbidden in key:
                    raise ValueError(
                        f"MCP server config key {key!r} must not contain {forbidden!r} "
                        f"(reserved for the canonical '{{server}}:{{tool}}' namespace and the "
                        f"LLM-API '__' rewrite)."
                    )
        return servers

    @model_validator(mode="after")
    def _validate_transports(self) -> McpSettings:
        for name, server in self.servers.items():
            if server.transport != "stdio":
                raise ValueError(
                    f"MCP server {name!r} has transport={server.transport!r}; "
                    f"only 'stdio' is supported in Sprint 2 "
                    f"(SSE / streamable-http are Sprint 2.1 candidates)."
                )
        return self


__all__ = [
    "McpSandboxConfig",
    "McpServerConfig",
    "McpSettings",
    "_command_auto_exempts_sandbox",
    "_substitute_env_placeholder",
]
