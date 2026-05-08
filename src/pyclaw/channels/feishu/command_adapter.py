from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pyclaw.core.agent.runtime_util import AgentAbortedError
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.registry import CommandRegistry, get_default_registry

if TYPE_CHECKING:
    from pyclaw.channels.feishu.handler import FeishuContext

logger = logging.getLogger(__name__)


def _split_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if " " in stripped:
        name, args = stripped.split(" ", 1)
        return name.lower(), args
    return stripped.lower(), ""


class FeishuCommandAdapter:
    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry = registry or get_default_registry()

    async def handle(
        self,
        text: str,
        *,
        session_key: str,
        session_id: str,
        message_id: str,
        event: Any,
        ctx: "FeishuContext",
    ) -> bool:
        if not text or not text.strip().startswith("/"):
            return False

        name, args = _split_command(text)
        spec = self._registry.get(name)
        if spec is None:
            return False

        sender = (
            event.event.sender if event.event and event.event.sender else None
        )
        open_id = (
            (sender.sender_id.open_id or "unknown")
            if sender and sender.sender_id
            else "unknown"
        )
        workspace_id = session_key.replace(":", "_")

        async def reply(reply_text: str) -> None:
            await ctx.feishu_client.reply_text(message_id, reply_text)

        async def dispatch_user_message(user_text: str) -> None:
            from pyclaw.channels.base import InboundMessage
            from pyclaw.channels.feishu.handler import _dispatch_and_reply

            new_sid = (
                await ctx.session_router.store.get_current_session_id(session_key)
                or session_id
            )
            workspace_path = ctx.workspace_base / workspace_id
            inbound = InboundMessage(
                session_id=new_sid,
                user_message=user_text,
                workspace_id=workspace_id,
                channel="feishu",
            )

            async def _run_followup() -> None:
                await _dispatch_and_reply(
                    inbound, ctx, message_id, workspace_path, ""
                )
                await ctx.session_router.update_last_interaction(new_sid)

            assert ctx.queue_registry is not None, (
                "queue_registry must be set on FeishuContext"
            )
            await ctx.queue_registry.enqueue(new_sid, _run_followup())

        cmd_ctx = CommandContext(
            session_id=session_id,
            session_key=session_key,
            workspace_id=workspace_id,
            user_id=open_id,
            channel="feishu",
            deps=ctx.deps,
            session_router=ctx.session_router,
            workspace_base=ctx.workspace_base,
            redis_client=ctx.redis_client,
            memory_store=ctx.memory_store,
            evolution_settings=ctx.evolution_settings,
            nudge_hook=ctx.nudge_hook,
            abort_event=asyncio.Event(),
            reply=reply,
            dispatch_user_message=dispatch_user_message,
            registry=self._registry,
            raw={
                "channel": "feishu",
                "feishu_event": event,
                "feishu_message_id": message_id,
                "feishu_queue_registry": ctx.queue_registry,
            },
        )

        try:
            return await self._registry.dispatch(name, args, cmd_ctx)
        except (asyncio.CancelledError, AgentAbortedError):
            raise
        except Exception:
            logger.exception("Command %s failed in feishu adapter", name)
            try:
                await reply(f"⚠️ 命令 {name} 执行失败，请稍后重试。")
            except Exception:
                logger.exception("Failed to send error reply for %s", name)
            return True
