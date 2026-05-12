"""Shared fixture helper for constructing CommandContext in tests.

Phase G introduced the required ``settings: Settings`` field on
CommandContext. Rather than updating 15+ test construction sites to each
build their own minimal settings object, tests import ``make_command_context``
and override only the fields they care about.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from pyclaw.core.commands.context import CommandContext
from pyclaw.infra.settings import Settings


async def _noop_reply(_text: str) -> None:
    return None


async def _noop_dispatch(_text: str) -> None:
    return None


def make_command_context(
    *,
    session_id: str = "ses_test",
    session_key: str = "test:user",
    workspace_id: str = "default",
    user_id: str = "user",
    channel: str = "test",
    deps: Any = None,
    session_router: Any = None,
    workspace_base: Path | None = None,
    reply: Callable[[str], Awaitable[None]] = _noop_reply,
    dispatch_user_message: Callable[[str], Awaitable[None]] = _noop_dispatch,
    raw: dict[str, Any] | None = None,
    settings: Settings | None = None,
    **overrides: Any,
) -> CommandContext:
    return CommandContext(
        session_id=session_id,
        session_key=session_key,
        workspace_id=workspace_id,
        user_id=user_id,
        channel=channel,
        deps=deps or MagicMock(),
        session_router=session_router or MagicMock(),
        workspace_base=workspace_base or Path("/tmp/test_workspace"),
        reply=reply,
        dispatch_user_message=dispatch_user_message,
        raw=raw if raw is not None else {},
        settings=settings or Settings(),
        **overrides,
    )
