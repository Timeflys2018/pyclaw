from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyclaw.channels.session_router import SessionRouter
    from pyclaw.core.agent.runner import AgentRunnerDeps
    from pyclaw.core.commands.registry import CommandRegistry


@dataclass
class CommandContext:
    session_id: str
    session_key: str
    workspace_id: str
    user_id: str
    channel: str

    deps: "AgentRunnerDeps"
    session_router: "SessionRouter"
    workspace_base: Path

    abort_event: asyncio.Event
    reply: Callable[[str], Awaitable[None]]
    dispatch_user_message: Callable[[str], Awaitable[None]]

    raw: dict[str, Any]

    redis_client: Any = None
    memory_store: Any = None
    evolution_settings: Any = None
    nudge_hook: Any = None
    registry: "CommandRegistry | None" = None
