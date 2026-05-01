from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from pyclaw.channels.base import InboundMessage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.models import AgentEvent


async def dispatch_message(
    inbound: InboundMessage,
    deps: AgentRunnerDeps,
    workspace_path: Path,
    extra_system: str = "",
) -> AsyncIterator[AgentEvent]:
    request = RunRequest(
        session_id=inbound.session_id,
        workspace_id=inbound.workspace_id,
        agent_id="default",
        user_message=inbound.user_message,
        attachments=inbound.attachments,
        extra_system=extra_system,
    )
    async for event in run_agent_stream(request, deps, tool_workspace_path=workspace_path):
        yield event
