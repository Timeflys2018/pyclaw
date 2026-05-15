from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from pyclaw.core.agent.runtime_util import AgentAbortedError
from pyclaw.core.commands._helpers import idle_guard_check
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.registry import CommandRegistry, get_default_registry

if TYPE_CHECKING:
    from pyclaw.channels.session_router import SessionRouter
    from pyclaw.channels.web.websocket import ConnectionState
    from pyclaw.core.agent.runner import AgentRunnerDeps
    from pyclaw.infra.settings import Settings

logger = logging.getLogger(__name__)


def _split_command(text: str) -> tuple[str, str]:
    stripped = text.strip()
    if " " in stripped:
        name, args = stripped.split(" ", 1)
        return name.lower(), args
    return stripped.lower(), ""


class WebCommandAdapter:
    def __init__(self, registry: CommandRegistry | None = None) -> None:
        self._registry = registry or get_default_registry()

    async def handle(
        self,
        text: str,
        *,
        state: ConnectionState,
        conversation_id: str,
        session_id: str,
        deps: AgentRunnerDeps,
        session_router: SessionRouter,
        workspace_base: Any,
        settings: Settings,
        redis_client: Any = None,
        memory_store: Any = None,
        evolution_settings: Any = None,
        nudge_hook: Any = None,
        session_queue: Any = None,
        agent_settings: Any = None,
        admin_user_ids: list[str] | None = None,
        worker_registry: Any = None,
        gateway_router: Any = None,
    ) -> bool:
        if not text or not text.strip().startswith("/"):
            return False

        name, args = _split_command(text)
        spec = self._registry.get(name)
        if spec is None:
            return False

        from pyclaw.channels.web.protocol import SERVER_CHAT_DONE
        from pyclaw.channels.web.websocket import send_event

        async def reply(reply_text: str) -> None:
            await send_event(
                state,
                SERVER_CHAT_DONE,
                conversation_id,
                {
                    "final_message": reply_text,
                    "usage": {},
                    "aborted": False,
                },
            )

        if session_queue is not None and await idle_guard_check(
            spec, session_queue, conversation_id, reply
        ):
            return True

        async def dispatch_user_message(user_text: str) -> None:
            from pyclaw.channels.web.chat import _run_chat
            from pyclaw.channels.web.protocol import ChatSendMessage

            if session_queue is None:
                raise RuntimeError(
                    "WebCommandAdapter requires session_queue for dispatch_user_message"
                )

            settings = state.ws.app.state.web_settings
            new_msg = ChatSendMessage(
                type="chat.send",
                conversation_id=conversation_id,
                content=user_text,
                attachments=[],
            )

            async def _handle(m: ChatSendMessage) -> None:
                await _run_chat(state, m, settings)

            await session_queue.enqueue(conversation_id, new_msg, _handle)

        user_id = state.user_id or "unknown"
        session_key = f"web:{user_id}"

        last_usage: dict[str, int] | None = None
        if session_queue is not None:
            get_usage = getattr(session_queue, "get_last_usage", None)
            if callable(get_usage):
                result = get_usage(conversation_id)
                if isinstance(result, dict):
                    last_usage = result

        cmd_ctx = CommandContext(
            session_id=session_id,
            session_key=session_key,
            workspace_id="default",
            user_id=user_id,
            channel="web",
            deps=deps,
            session_router=session_router,
            workspace_base=workspace_base,
            settings=settings,
            redis_client=redis_client,
            memory_store=memory_store,
            evolution_settings=evolution_settings,
            nudge_hook=nudge_hook,
            agent_settings=agent_settings,
            reply=reply,
            dispatch_user_message=dispatch_user_message,
            registry=self._registry,
            raw={
                "channel": "web",
                "web_state": state,
                "web_conversation_id": conversation_id,
                "conversation_id": conversation_id,
                "tool_workspace_path": workspace_base / f"web_{user_id}",
            },
            session_queue=session_queue,
            admin_user_ids=list(admin_user_ids or []),
            last_usage=last_usage,
            worker_registry=worker_registry,
            gateway_router=gateway_router,
        )

        try:
            return await self._registry.dispatch(name, args, cmd_ctx)
        except (asyncio.CancelledError, AgentAbortedError):
            raise
        except Exception:
            logger.exception("Command %s failed in web adapter", name)
            try:
                await reply(f"⚠️ 命令 {name} 执行失败，请稍后重试。")
            except Exception:
                logger.exception("Failed to send error reply for %s", name)
            return True
