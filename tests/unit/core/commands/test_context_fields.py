"""Tests for CommandContext additive fields (Phase A6)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from pyclaw.core.commands.context import CommandContext


def _minimal_context(**overrides) -> CommandContext:
    kwargs = dict(
        session_id="s1",
        session_key="web:user_x",
        workspace_id="ws",
        user_id="user_x",
        channel="web",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        reply=AsyncMock(),
        dispatch_user_message=AsyncMock(),
        raw={},
    )
    kwargs.update(overrides)
    return CommandContext(**kwargs)


def test_queue_registry_default_none() -> None:
    ctx = _minimal_context()
    assert ctx.queue_registry is None


def test_session_queue_default_none() -> None:
    ctx = _minimal_context()
    assert ctx.session_queue is None


def test_both_fields_settable_as_kwargs() -> None:
    qr = MagicMock()
    sq = MagicMock()
    ctx = _minimal_context(queue_registry=qr, session_queue=sq)
    assert ctx.queue_registry is qr
    assert ctx.session_queue is sq


def test_existing_positional_order_still_works() -> None:
    """Additive fields at the end must not break existing keyword construction."""
    ctx = _minimal_context(command_timeout=5.0)
    assert ctx.command_timeout == 5.0
    assert ctx.queue_registry is None
    assert ctx.session_queue is None
