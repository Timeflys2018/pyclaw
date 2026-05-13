from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyclaw.core.commands.builtin import cmd_help, register_builtin_commands
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.registry import CommandRegistry


@pytest.mark.asyncio
async def test_help_lists_steer_and_btw_under_steering_category():
    registry = CommandRegistry()
    register_builtin_commands(registry)

    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    ctx = CommandContext(
        session_id="sess",
        session_key="key",
        workspace_id="ws",
        user_id="u",
        channel="web",
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        reply=reply,
        dispatch_user_message=lambda _t: None,  # type: ignore
        raw={},
        settings=MagicMock(),
        registry=registry,
    )

    await cmd_help("", ctx)

    assert len(replies) == 1
    output = replies[0]

    assert "📂 steering" in output
    assert "/steer" in output
    assert "/btw" in output
    assert "<message>" in output
    assert "<question>" in output


@pytest.mark.asyncio
async def test_help_groups_commands_by_category():
    registry = CommandRegistry()
    register_builtin_commands(registry)
    grouped = registry.list_by_category()

    assert "steering" in grouped
    steering_specs = {s.name for s in grouped["steering"]}
    assert steering_specs == {"/steer", "/btw"}
