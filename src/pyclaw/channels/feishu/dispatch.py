from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

from pyclaw.channels.base import InboundMessage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.models import AgentEvent

if TYPE_CHECKING:
    from pyclaw.channels.feishu.queue import FeishuQueueRegistry


async def dispatch_message(
    inbound: InboundMessage,
    deps: AgentRunnerDeps,
    workspace_path: Path,
    extra_system: str = "",
    *,
    queue_registry: "FeishuQueueRegistry | None" = None,
) -> AsyncIterator[AgentEvent]:
    request = RunRequest(
        session_id=inbound.session_id,
        workspace_id=inbound.workspace_id,
        agent_id="default",
        user_message=inbound.user_message,
        attachments=inbound.attachments,
        extra_system=extra_system,
    )
    rc = queue_registry.get_run_control(inbound.session_id) if queue_registry is not None else None
    if rc is not None:
        rc.active = True
    try:
        async for event in run_agent_stream(
            request, deps, tool_workspace_path=workspace_path, control=rc,
        ):
            yield event
    finally:
        if rc is not None:
            rc.active = False
