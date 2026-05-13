from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyclaw.channels.session_router import SessionRouter
    from pyclaw.core.agent.runner import AgentRunnerDeps
    from pyclaw.core.commands.registry import CommandRegistry
    from pyclaw.infra.settings import Settings


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

    reply: Callable[[str], Awaitable[None]]
    dispatch_user_message: Callable[[str], Awaitable[None]]

    raw: dict[str, Any]

    settings: "Settings"

    redis_client: Any = None
    memory_store: Any = None
    evolution_settings: Any = None
    nudge_hook: Any = None
    agent_settings: Any = None
    registry: "CommandRegistry | None" = None
    command_timeout: float = 30.0
    queue_registry: Any = None
    session_queue: Any = None
    admin_user_ids: list[str] = field(default_factory=list)
    last_usage: dict[str, int] | None = None
