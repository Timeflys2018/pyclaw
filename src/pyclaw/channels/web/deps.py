from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from pyclaw.channels.session_router import SessionRouter
    from pyclaw.channels.web.chat import SessionQueue
    from pyclaw.channels.web.websocket import ConnectionRegistry
    from pyclaw.core.agent.runner import AgentRunnerDeps
    from pyclaw.gateway.worker_registry import WorkerRegistry
    from pyclaw.storage.session.base import SessionStore


@dataclass
class WebDeps:
    session_store: "SessionStore"
    session_router: "SessionRouter"
    workspace_base: Path
    runner_deps: "AgentRunnerDeps"
    session_queue: "SessionQueue"
    connection_registry: "ConnectionRegistry"

    redis_client: Any = None
    memory_store: Any = None
    task_manager: Any = None
    evolution_settings: Any = None
    nudge_hook: Any = None
    llm_client: Any = None
    agent_settings: Any = None
    worker_registry: "WorkerRegistry | None" = None
    admin_user_ids: list[str] = field(default_factory=list)


def get_web_deps(request: Request) -> WebDeps:
    deps = getattr(request.app.state, "web_deps", None)
    if deps is None:
        from fastapi import HTTPException
        raise HTTPException(500, "Web deps not configured")
    return deps
