from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from pyclaw.auth.tools_requiring_approval import resolve_tools_requiring_approval
from pyclaw.core.hooks import ApprovalDecision, PermissionTier
from pyclaw.infra.audit_logger import AuditLogger
from pyclaw.infra.settings import WebSettings

if TYPE_CHECKING:
    from pyclaw.channels.web.chat import SessionQueue

logger = logging.getLogger(__name__)


class WebToolApprovalHook:
    """Concrete :class:`ToolApprovalHook` for the Web channel.

    Activated by the runner under ``approval`` tier. Per design D6, uses
    ``asyncio.Event`` (not polling) to await each pending decision via
    :class:`pyclaw.channels.web.chat.PendingDecision` returned from
    :meth:`SessionQueue.create_pending`.

    Behaviour (post Sprint 2.0.1 hotfix — runner-side partition):
    - The runner only calls this hook with the subset of approval-tier calls
      that ``should_gate(name)`` returned True for, OR that are
      ``forced-by-server-config``. Non-gated calls are auto-approved by the
      runner without emitting ``tool.approve_request``.
    - Each gated call creates a :class:`PendingDecision`, then waits on it
      with ``WebSettings.tool_approval_timeout_seconds`` timeout. The
      ``tool.approve_request`` event is emitted by the runner before this
      hook is invoked, so we just wait for the matching ``tool.approve``.
    - Timeout → ``deny`` + audit ``decided_by="auto:timeout"`` per design D13.
    - Each decision is logged via :class:`AuditLogger`.
    """

    def __init__(
        self,
        *,
        session_queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        self._queue = session_queue
        self._settings = settings
        self._audit = audit_logger

    def should_gate(self, tool_name: str, ctx: Any = None) -> bool:
        profile = getattr(ctx, "user_profile", None) if ctx is not None else None
        effective = resolve_tools_requiring_approval(
            profile=profile,
            channel_default=self._settings.tools_requiring_approval,
        )
        return tool_name in effective

    async def before_tool_execution(
        self,
        tool_calls: list[dict],
        session_id: str,
        tier: PermissionTier,
    ) -> list[ApprovalDecision]:
        decisions: list[ApprovalDecision] = []

        for call in tool_calls:
            tool_name = call.get("name", "") or ""
            tool_call_id = call.get("id", "") or ""

            decision = await self._wait_for_user_decision(
                session_id=session_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tier=tier,
            )
            decisions.append(decision)

        return decisions

    async def _wait_for_user_decision(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_call_id: str,
        tier: PermissionTier,
    ) -> ApprovalDecision:
        pending = self._queue.create_pending(session_id, tool_call_id)
        timeout = self._settings.tool_approval_timeout_seconds
        started = time.monotonic()

        try:
            approved = await pending.wait_decision(timeout_seconds=timeout)
        except TimeoutError:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            self._audit.log_decision(
                conv_id=session_id,
                session_id=session_id,
                channel="web",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tier=tier,
                decision="deny",
                decided_by="auto:timeout",
                elapsed_ms=elapsed_ms,
            )
            return "deny"
        finally:
            self._queue.discard_pending(session_id, tool_call_id)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        decision: ApprovalDecision = "approve" if approved else "deny"
        self._audit.log_decision(
            conv_id=session_id,
            session_id=session_id,
            channel="web",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tier=tier,
            decision=decision,
            decided_by="user",
            elapsed_ms=elapsed_ms,
        )
        return decision
