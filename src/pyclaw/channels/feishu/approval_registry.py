from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class FeishuPendingDecision:
    """Awaitable handle for a single pending Feishu CardKit approval.

    Created by :class:`FeishuToolApprovalHook` when posting an approval card.
    Resolved by :meth:`FeishuApprovalRegistry.set_decision` when the originator
    clicks Approve/Deny in the card, or by the hook's ``asyncio.wait_for``
    timeout. ``approved`` stays ``None`` until ``event`` is set.

    Also stores ``message_id`` (returned by ``card.create``) and
    ``originator_open_id`` (validated on every callback) plus the per-decision
    audit metadata propagated back to the hook for logging.
    """

    event: asyncio.Event = field(default_factory=asyncio.Event)
    approved: bool | None = None
    message_id: str | None = None
    card_id: str | None = None
    originator_open_id: str = ""
    operator_open_id: str | None = None

    async def wait_decision(self, timeout_seconds: float) -> bool:
        await asyncio.wait_for(self.event.wait(), timeout=timeout_seconds)
        return bool(self.approved)


class FeishuApprovalRegistry:
    """Lifespan-scoped in-memory store of pending Feishu approvals.

    Per design D8, the Feishu channel does NOT reuse the Web ``SessionQueue``
    (which is WebSocket-coupled). This registry is a dedicated per-channel
    store keyed by ``(conv_id, tool_call_id)``. Singleton constructed in
    ``app.py`` lifespan, injected into ``FeishuToolApprovalHook`` and the
    card-action callback handler.
    """

    def __init__(self) -> None:
        self._pending: dict[tuple[str, str], FeishuPendingDecision] = {}

    def create_pending(
        self,
        *,
        conv_id: str,
        tool_call_id: str,
        originator_open_id: str,
    ) -> FeishuPendingDecision:
        key = (conv_id, tool_call_id)
        pending = FeishuPendingDecision(
            originator_open_id=originator_open_id,
        )
        self._pending[key] = pending
        return pending

    def get(
        self,
        *,
        conv_id: str,
        tool_call_id: str,
    ) -> FeishuPendingDecision | None:
        return self._pending.get((conv_id, tool_call_id))

    def discard(self, *, conv_id: str, tool_call_id: str) -> None:
        self._pending.pop((conv_id, tool_call_id), None)

    def set_decision(
        self,
        *,
        conv_id: str,
        tool_call_id: str,
        approved: bool,
        operator_open_id: str | None = None,
    ) -> bool:
        pending = self._pending.get((conv_id, tool_call_id))
        if pending is None:
            return False
        pending.approved = approved
        pending.operator_open_id = operator_open_id
        pending.event.set()
        return True

    def reset(self) -> None:
        for pending in list(self._pending.values()):
            pending.approved = False
            pending.event.set()
        self._pending.clear()
