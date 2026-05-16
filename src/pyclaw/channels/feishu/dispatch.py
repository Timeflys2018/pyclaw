from __future__ import annotations

import dataclasses
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pyclaw.channels.base import InboundMessage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.hooks import PermissionTier
from pyclaw.models import AgentEvent, Done

if TYPE_CHECKING:
    from pyclaw.channels.feishu.queue import FeishuQueueRegistry


async def dispatch_message(
    inbound: InboundMessage,
    deps: AgentRunnerDeps,
    workspace_path: Path,
    extra_system: str = "",
    *,
    queue_registry: "FeishuQueueRegistry | None" = None,
    tool_approval_hook: Any = None,
    permission_tier_override: PermissionTier | None = None,
    audit_logger: Any = None,
    user_id: str | None = None,
    role: str | None = None,
    user_profile: Any = None,
    sandbox_policy: Any = None,
) -> AsyncIterator[AgentEvent]:
    request = RunRequest(
        session_id=inbound.session_id,
        workspace_id=inbound.workspace_id,
        agent_id="default",
        user_message=inbound.user_message,
        attachments=inbound.attachments,
        extra_system=extra_system,
        permission_tier_override=permission_tier_override,
        user_id=user_id,
        role=role,  # type: ignore[arg-type]
        user_profile=user_profile,
        sandbox_policy=sandbox_policy,
    )
    if tool_approval_hook is not None or audit_logger is not None:
        if dataclasses.is_dataclass(deps) and not isinstance(deps, type):
            replace_kwargs: dict[str, Any] = {"channel": "feishu"}
            if tool_approval_hook is not None:
                replace_kwargs["tool_approval_hook"] = tool_approval_hook
            if audit_logger is not None:
                replace_kwargs["audit_logger"] = audit_logger
            deps = dataclasses.replace(deps, **replace_kwargs)
        else:
            try:
                if tool_approval_hook is not None:
                    deps.tool_approval_hook = tool_approval_hook
                if audit_logger is not None:
                    deps.audit_logger = audit_logger
                deps.channel = "feishu"
            except (AttributeError, TypeError):
                pass
    rc = queue_registry.get_run_control(inbound.session_id) if queue_registry is not None else None
    if rc is not None:
        rc.active = True
    try:
        async for event in run_agent_stream(
            request,
            deps,
            tool_workspace_path=workspace_path,
            control=rc,
        ):
            if isinstance(event, Done) and queue_registry is not None and event.usage:
                set_usage = getattr(queue_registry, "set_last_usage", None)
                if callable(set_usage):
                    set_usage(inbound.session_id, event.usage)
            yield event
    finally:
        if rc is not None:
            rc.active = False
