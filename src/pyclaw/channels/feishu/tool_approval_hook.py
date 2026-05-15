from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from pyclaw.channels.feishu.approval_card import (
    build_approval_card,
    build_countdown_card,
    build_resolved_card,
)
from pyclaw.channels.feishu.approval_registry import FeishuApprovalRegistry
from pyclaw.core.hooks import ApprovalDecision, PermissionTier
from pyclaw.infra.audit_logger import AuditLogger
from pyclaw.infra.settings import FeishuSettings
from pyclaw.infra.task_manager import TaskManager

if TYPE_CHECKING:
    from pyclaw.channels.feishu.client import FeishuClient

logger = logging.getLogger(__name__)


class FeishuToolApprovalHook:
    """Concrete :class:`ToolApprovalHook` for the Feishu channel.

    Posts a CardKit interactive card with Approve/Deny buttons + countdown,
    waits on :class:`FeishuPendingDecision.event` (set by the card-action
    callback handler), and on decision/timeout patches the card to a terminal
    state. Originator-only authorization is enforced by the callback handler,
    not here.

    Per design D7/D8/D13: countdown patches every 5s, message posted via
    ``cardkit.v1.card.acreate`` + ``im.v1.message.areply``, terminal state
    written via ``cardkit.v1.card_element.acontent`` (existing CardKit API).
    """

    def __init__(
        self,
        *,
        client: FeishuClient,
        registry: FeishuApprovalRegistry,
        settings: FeishuSettings,
        audit_logger: AuditLogger,
        task_manager: TaskManager | None = None,
    ) -> None:
        self._client = client
        self._registry = registry
        self._settings = settings
        self._audit = audit_logger
        self._task_manager = task_manager
        self._originator_resolver: Any = None

    def set_originator_resolver(self, resolver: Any) -> None:
        self._originator_resolver = resolver

    async def before_tool_execution(
        self,
        tool_calls: list[dict],
        session_id: str,
        tier: PermissionTier,
    ) -> list[ApprovalDecision]:
        decisions: list[ApprovalDecision] = []
        gated = set(self._settings.tools_requiring_approval)
        originator_open_id = self._resolve_originator(session_id)

        for call in tool_calls:
            tool_name = call.get("name", "") or ""
            tool_call_id = call.get("id", "") or ""

            if tool_name not in gated:
                self._audit.log_decision(
                    conv_id=session_id,
                    session_id=session_id,
                    channel="feishu",
                    tool_name=tool_name,
                    tool_call_id=tool_call_id,
                    tier=tier,
                    decision="approve",
                    decided_by="auto:not-gated",
                )
                decisions.append("approve")
                continue

            decision = await self._wait_for_user_decision(
                session_id=session_id,
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tier=tier,
                originator_open_id=originator_open_id,
                args=call.get("args") or {},
            )
            decisions.append(decision)

        return decisions

    def _resolve_originator(self, session_id: str) -> str:
        if self._originator_resolver is not None:
            try:
                return self._originator_resolver(session_id) or ""
            except Exception:
                logger.warning("originator_resolver failed for %s", session_id, exc_info=True)
        parts = session_id.split(":")
        if len(parts) >= 3 and parts[0] == "feishu":
            return parts[2]
        return ""

    async def _wait_for_user_decision(
        self,
        *,
        session_id: str,
        tool_name: str,
        tool_call_id: str,
        tier: PermissionTier,
        originator_open_id: str,
        args: dict[str, Any],
    ) -> ApprovalDecision:
        timeout = self._settings.tool_approval_timeout_seconds
        pending = self._registry.create_pending(
            conv_id=session_id,
            tool_call_id=tool_call_id,
            originator_open_id=originator_open_id,
        )
        started = time.monotonic()

        description = self._format_args(tool_name, args)
        try:
            await self._post_card(
                pending=pending,
                conv_id=session_id,
                tool_call_id=tool_call_id,
                originator_open_id=originator_open_id,
                tool_name=tool_name,
                description=description,
                countdown_seconds=timeout,
            )
        except Exception:
            logger.exception("failed to post approval card; auto-deny")
            self._registry.discard(conv_id=session_id, tool_call_id=tool_call_id)
            self._audit.log_decision(
                conv_id=session_id,
                session_id=session_id,
                channel="feishu",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tier=tier,
                decision="deny",
                decided_by="auto:post-failed",
            )
            return "deny"

        countdown_task_id: str | None = None
        if self._task_manager is not None:
            countdown_task_id = self._task_manager.spawn(
                f"feishu-approval-countdown:{session_id}:{tool_call_id}",
                self._countdown_loop(
                    pending=pending,
                    conv_id=session_id,
                    tool_call_id=tool_call_id,
                    originator_open_id=originator_open_id,
                    tool_name=tool_name,
                    description=description,
                    total_seconds=timeout,
                ),
                category="generic",
                owner=session_id,
            )

        try:
            approved = await pending.wait_decision(timeout_seconds=timeout)
        except TimeoutError:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            await self._patch_terminal_card(pending, tool_name, "timeout")
            self._audit.log_decision(
                conv_id=session_id,
                session_id=session_id,
                channel="feishu",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                tier=tier,
                decision="deny",
                decided_by="auto:timeout",
                elapsed_ms=elapsed_ms,
            )
            return "deny"
        finally:
            if countdown_task_id and self._task_manager is not None:
                await self._task_manager.cancel(countdown_task_id, timeout=2.0)
            self._registry.discard(conv_id=session_id, tool_call_id=tool_call_id)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        decision: ApprovalDecision = "approve" if approved else "deny"
        await self._patch_terminal_card(
            pending,
            tool_name,
            decision,
            operator_open_id=pending.operator_open_id,
        )
        self._audit.log_decision(
            conv_id=session_id,
            session_id=session_id,
            channel="feishu",
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tier=tier,
            decision=decision,
            decided_by=pending.operator_open_id or "user",
            elapsed_ms=elapsed_ms,
        )
        return decision

    @staticmethod
    def _format_args(tool_name: str, args: dict[str, Any]) -> str:
        if not args:
            return "_(no arguments)_"
        snippets: list[str] = []
        for k, v in list(args.items())[:5]:
            value_str = str(v)
            if len(value_str) > 200:
                value_str = value_str[:200] + "…"
            snippets.append(f"- **{k}**: `{value_str}`")
        return "\n".join(snippets)

    async def _post_card(
        self,
        *,
        pending: Any,
        conv_id: str,
        tool_call_id: str,
        originator_open_id: str,
        tool_name: str,
        description: str,
        countdown_seconds: int,
    ) -> None:
        card_json = build_approval_card(
            conv_id=conv_id,
            tool_call_id=tool_call_id,
            originator_open_id=originator_open_id,
            tool_name=tool_name,
            description=description,
            countdown_seconds=countdown_seconds,
        )
        receive_id, receive_id_type = self._resolve_receive_id(
            conv_id,
            originator_open_id,
        )
        message_id = await self._client.send_interactive_card(
            receive_id=receive_id,
            receive_id_type=receive_id_type,
            card_json=card_json,
        )
        if message_id is None:
            raise RuntimeError(
                f"Feishu send_interactive_card returned None "
                f"(receive_id_type={receive_id_type}); see client warning logs"
            )
        pending.message_id = message_id

    @staticmethod
    def _resolve_receive_id(conv_id: str, originator_open_id: str) -> tuple[str, str]:
        parts = conv_id.split(":")
        if len(parts) >= 4 and parts[0] == "feishu" and parts[2] == "chat":
            return parts[3], "chat_id"
        if originator_open_id:
            return originator_open_id, "open_id"
        if len(parts) >= 3 and parts[0] == "feishu":
            return parts[2], "open_id"
        return conv_id, "open_id"

    async def _countdown_loop(
        self,
        *,
        pending: Any,
        conv_id: str,
        tool_call_id: str,
        originator_open_id: str,
        tool_name: str,
        description: str,
        total_seconds: int,
    ) -> None:
        interval = 5.0
        elapsed = 0.0
        try:
            while elapsed + interval < total_seconds:
                await asyncio.sleep(interval)
                elapsed += interval
                if pending.event.is_set():
                    return
                remaining = max(int(total_seconds - elapsed), 0)
                try:
                    new_json = build_countdown_card(
                        conv_id=conv_id,
                        tool_call_id=tool_call_id,
                        originator_open_id=originator_open_id,
                        tool_name=tool_name,
                        description=description,
                        remaining_seconds=remaining,
                    )
                    await self._client.patch_interactive_card(
                        message_id=pending.message_id or "",
                        card_json=new_json,
                    )
                except Exception:
                    logger.warning(
                        "countdown patch failed for %s:%s",
                        conv_id,
                        tool_call_id,
                        exc_info=True,
                    )
        except asyncio.CancelledError:
            return

    async def _patch_terminal_card(
        self,
        pending: Any,
        tool_name: str,
        decision: str,
        operator_open_id: str | None = None,
    ) -> None:
        if not pending.message_id:
            return
        try:
            terminal_json = build_resolved_card(
                tool_name=tool_name,
                decision=decision,
                operator_open_id=operator_open_id,
            )
            await self._client.patch_interactive_card(
                message_id=pending.message_id,
                card_json=terminal_json,
            )
        except Exception:
            logger.warning(
                "terminal-card patch failed (decision=%s, msg=%s)",
                decision,
                pending.message_id,
                exc_info=True,
            )
