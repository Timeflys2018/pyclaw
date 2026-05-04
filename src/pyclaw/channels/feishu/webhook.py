from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path

import lark_oapi as lark
import lark_oapi.ws.client as _ws_client_module
from lark_oapi import EventDispatcherHandler
from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1

from pyclaw.channels.feishu.client import FeishuClient
from pyclaw.channels.feishu.dedup import FeishuDedup
from pyclaw.channels.feishu.handler import FeishuContext, handle_feishu_message
from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.infra.settings import FeishuSettings
from pyclaw.storage.workspace.base import WorkspaceStore

logger = logging.getLogger(__name__)


class FeishuChannelPlugin:
    name = "feishu"

    def __init__(
        self,
        settings: FeishuSettings,
        feishu_client: FeishuClient,
        deps: AgentRunnerDeps,
        dedup: FeishuDedup,
        workspace_store: WorkspaceStore,
        bootstrap_files: list[str] | None = None,
        workspace_base: Path | None = None,
    ) -> None:
        self._settings = settings
        self._feishu_client = feishu_client
        self._deps = deps
        self._dedup = dedup
        self._workspace_store = workspace_store
        self._bootstrap_files: list[str] = bootstrap_files or ["AGENTS.md"]
        self._workspace_base: Path = workspace_base or Path.home() / ".pyclaw/workspaces"
        self._ws_client: lark.ws.Client | None = None
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._ws_loop: asyncio.AbstractEventLoop | None = None
        self._ws_thread: threading.Thread | None = None
        self._status: str = "disconnected"

    @property
    def status(self) -> str:
        return self._status

    async def start(self) -> None:
        from pyclaw.storage.session.base import SessionStore as SessionStoreProtocol
        if not isinstance(self._deps.session_store, SessionStoreProtocol):
            raise TypeError(
                f"session_store {type(self._deps.session_store).__name__!r} does not implement "
                "the full SessionStore protocol (missing get_current_session_id / "
                "create_new_session / list_session_history)"
            )

        bot_open_id = await self._feishu_client.probe_bot_identity()
        logger.info("Feishu bot open_id: %s", bot_open_id)

        main_loop = asyncio.get_event_loop()
        self._main_loop = main_loop

        from pyclaw.channels.feishu.queue import FeishuQueueRegistry

        assert self._deps.task_manager is not None, "Feishu channel requires task_manager in AgentRunnerDeps"
        queue_registry = FeishuQueueRegistry(task_manager=self._deps.task_manager)
        session_router = SessionRouter(
            store=self._deps.session_store,
            on_session_rotated=queue_registry.cleanup_session,
        )
        ctx = FeishuContext(
            settings=self._settings,
            feishu_client=self._feishu_client,
            deps=self._deps,
            dedup=self._dedup,
            workspace_store=self._workspace_store,
            bot_open_id=bot_open_id,
            session_router=session_router,
            workspace_base=self._workspace_base,
            bootstrap_files=self._bootstrap_files,
            queue_registry=queue_registry,
        )

        def _sync_handler(event: P2ImMessageReceiveV1) -> None:
            asyncio.run_coroutine_threadsafe(
                handle_feishu_message(event, ctx),
                main_loop,
            )

        dispatcher = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_sync_handler)
            .register_p2_im_message_message_read_v1(lambda _: None)
            .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(lambda _: None)
            .build()
        )

        self._ws_client = lark.ws.Client(
            self._settings.app_id,
            self._settings.app_secret,
            event_handler=dispatcher,
            auto_reconnect=True,
        )

        ws_loop = asyncio.new_event_loop()
        self._ws_loop = ws_loop

        # MONKEY-PATCH: lark-oapi's ws.Client.start() calls
        # run_until_complete() on a module-level asyncio loop.  We replace
        # it with our own event loop running in a dedicated thread so that
        # the WS client doesn't block the main event loop.  Remove this
        # when lark-oapi ships a native async WS client.
        _ws_client_module.loop = ws_loop

        def _run_ws() -> None:
            asyncio.set_event_loop(ws_loop)
            try:
                self._ws_client.start()
            except RuntimeError as exc:
                if "Event loop stopped" in str(exc) or "Event loop is closed" in str(exc):
                    logger.debug("Feishu WS thread stopped (shutdown): %s", exc)
                else:
                    logger.exception("Feishu WS thread exited with error")
            except Exception:
                logger.exception("Feishu WS thread exited with error")

        thread = threading.Thread(target=_run_ws, daemon=True, name="feishu-ws")
        self._ws_thread = thread
        thread.start()

        self._status = "connected"
        logger.info("Feishu WS thread started")

    async def stop(self) -> None:
        self._status = "disconnected"
        if self._ws_loop is not None and not self._ws_loop.is_closed():
            self._ws_loop.call_soon_threadsafe(self._ws_loop.stop)
        if self._ws_thread is not None:
            self._ws_thread.join(timeout=5.0)
            if self._ws_thread.is_alive():
                logger.warning("Feishu WS thread did not terminate within 5s")
        logger.info("Feishu channel stopped")
