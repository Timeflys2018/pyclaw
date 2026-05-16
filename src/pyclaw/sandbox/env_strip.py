from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Iterable

DEFAULT_ALLOWLIST: frozenset[str] = frozenset({"PATH", "HOME", "LANG", "TERM"})

HARDCODED_DENY_PREFIXES: tuple[str, ...] = (
    "ANTHROPIC_",
    "PYCLAW_",
    "LITELLM_",
)

HARDCODED_DENY_NAMES: frozenset[str] = frozenset(
    {
        "ANTHROPIC_API_KEY",
        "PYCLAW_SECRET",
        "PYCLAW_LLM_API_KEY",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "AWS_SESSION_TOKEN",
        "OPENAI_API_KEY",
        "LITELLM_PROXY",
        "LITELLM_LOG",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "KUBECONFIG",
        "KUBE_TOKEN",
        "LD_PRELOAD",
        "LD_LIBRARY_PATH",
        "LD_AUDIT",
        "DYLD_INSERT_LIBRARIES",
        "DYLD_LIBRARY_PATH",
        "DYLD_FALLBACK_LIBRARY_PATH",
    }
)

_GLOB_DENY_PREFIX_REJECTIONS: tuple[str, ...] = (
    "ANTHROPIC_",
    "PYCLAW_",
    "AWS_",
    "OPENAI_",
    "LITELLM_",
    "SSH_",
    "KUBE_",
    "GH_",
    "GITHUB_",
)


def _is_hardcoded_deny(name: str) -> bool:
    if name in HARDCODED_DENY_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in HARDCODED_DENY_PREFIXES)


def validate_env_allowlist(allowlist: Iterable[str]) -> None:
    """Raise ``ValueError`` for entries that would defeat the hardcoded deny floor.

    Glob patterns matching a deny prefix (e.g. ``"AWS_*"``) are rejected at
    config-load time per Sprint 3 4-slot review F10. The bare wildcard ``"*"``
    is permitted because the deny floor still strips dangerous names.
    """
    for entry in allowlist:
        if not isinstance(entry, str):
            raise ValueError(f"env_allowlist entry must be str, got {type(entry).__name__}")
        if entry == "*":
            continue
        if "*" in entry or "?" in entry:
            for prefix in _GLOB_DENY_PREFIX_REJECTIONS:
                if entry.startswith(prefix):
                    raise ValueError(
                        f"env_allowlist glob pattern {entry!r} is not allowed because it "
                        f"matches the hardcoded deny prefix {prefix!r}; use a specific "
                        f"variable name instead (e.g. 'AWS_REGION')"
                    )
            raise ValueError(
                f"env_allowlist entries must be specific names or '*'; glob {entry!r} not supported"
            )


def build_clean_env(
    *,
    allowlist: Iterable[str],
    parent_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Construct a child-process env from ``parent_env`` filtered by ``allowlist``.

    Always passes ``DEFAULT_ALLOWLIST`` (PATH/HOME/LANG/TERM) plus user-specified
    names. The wildcard ``"*"`` admits all parent env vars that aren't in the
    hardcoded deny floor.
    """
    source: Mapping[str, str] = parent_env if parent_env is not None else os.environ
    user_allow = set(allowlist or ())

    env: dict[str, str] = {}

    for name in DEFAULT_ALLOWLIST:
        if name in source and not _is_hardcoded_deny(name):
            env[name] = source[name]

    if "*" in user_allow:
        for name, value in source.items():
            if not _is_hardcoded_deny(name):
                env[name] = value
        return env

    for name in user_allow:
        if name in source and not _is_hardcoded_deny(name):
            env[name] = source[name]

    return env
