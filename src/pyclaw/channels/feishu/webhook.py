from __future__ import annotations

import asyncio
import logging
import threading
from pathlib import Path
from typing import Any

import lark_oapi as lark
import lark_oapi.ws.client as _ws_client_module
from lark_oapi import EventDispatcherHandler
from lark_oapi.api.im.v1.model import P2ImMessageReactionCreatedV1, P2ImMessageReceiveV1

from pyclaw.channels.feishu.client import FeishuClient
from pyclaw.channels.feishu.dedup import FeishuDedup
from pyclaw.channels.feishu.handler import (
    FeishuContext,
    handle_feishu_message,
    handle_feishu_reaction_created,
)
from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.core.memory_archive import archive_session_background
from pyclaw.infra.settings import FeishuSettings, Settings
from pyclaw.storage.memory.base import MemoryStore
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
        *,
        settings_full: Settings,
        bootstrap_files: list[str] | None = None,
        workspace_base: Path | None = None,
        memory_store: MemoryStore | None = None,
        redis_client: Any = None,  # noqa: ANN401
        evolution_settings: Any = None,  # noqa: ANN401
        agent_settings: Any = None,  # noqa: ANN401
        admin_user_ids: list[str] | None = None,
        gateway_router: Any = None,  # noqa: ANN401
        worker_registry: Any = None,  # noqa: ANN401
    ) -> None:
        self._settings = settings
        self._settings_full = settings_full
        self._feishu_client = feishu_client
        self._deps = deps
        self._dedup = dedup
        self._workspace_store = workspace_store
        self._bootstrap_files: list[str] = bootstrap_files or ["AGENTS.md"]
        self._workspace_base: Path = workspace_base or Path.home() / ".pyclaw/workspaces"
        self._memory_store = memory_store
        self._redis_client = redis_client
        self._evolution_settings = evolution_settings
        self._agent_settings = agent_settings
        self._admin_user_ids: list[str] = list(admin_user_ids or [])
        self._gateway_router = gateway_router
        self._worker_registry = worker_registry
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

        memory_store = self._memory_store
        session_store = self._deps.session_store
        task_manager = self._deps.task_manager

        redis_client = self._redis_client
        llm_client = self._deps.llm
        evolution_settings = self._evolution_settings
        from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook

        nudge_hook = next(
            (h for h in self._deps.hooks.hooks() if isinstance(h, MemoryNudgeHook)),
            None,
        )

        async def _on_rotated(old_session_id: str) -> None:
            try:
                await queue_registry.cleanup_session(old_session_id)
            except Exception:
                logger.warning("queue cleanup failed for %s", old_session_id, exc_info=True)
            if memory_store is not None:
                archive_owner = (
                    old_session_id.split(":s:", 1)[0]
                    if ":s:" in old_session_id
                    else None
                )
                task_manager.spawn(
                    f"archive:{old_session_id}",
                    archive_session_background(memory_store, session_store, old_session_id),
                    category="archive",
                    owner=archive_owner,
                )
                if (
                    redis_client is not None
                    and evolution_settings is not None
                    and getattr(evolution_settings, "enabled", False)
                ):
                    from pyclaw.core.sop_extraction import maybe_spawn_extraction

                    await maybe_spawn_extraction(
                        task_manager=task_manager,
                        memory_store=memory_store,
                        session_store=session_store,
                        redis_client=redis_client,
                        llm_client=llm_client,
                        session_id=old_session_id,
                        settings=evolution_settings,
                        nudge_hook=nudge_hook,
                    )

        session_router = SessionRouter(
            store=self._deps.session_store,
            on_session_rotated=_on_rotated,
        )
        ctx = FeishuContext(
            settings=self._settings,
            settings_full=self._settings_full,
            feishu_client=self._feishu_client,
            deps=self._deps,
            dedup=self._dedup,
            workspace_store=self._workspace_store,
            bot_open_id=bot_open_id,
            session_router=session_router,
            workspace_base=self._workspace_base,
            bootstrap_files=self._bootstrap_files,
            queue_registry=queue_registry,
            redis_client=redis_client,
            memory_store=memory_store,
            evolution_settings=evolution_settings,
            nudge_hook=nudge_hook,
            agent_settings=self._agent_settings,
            admin_user_ids=self._admin_user_ids,
            gateway_router=self._gateway_router,
            task_manager=task_manager,
            worker_registry=self._worker_registry,
        )
        self._ctx = ctx

        def _sync_handler(event: P2ImMessageReceiveV1) -> None:
            asyncio.run_coroutine_threadsafe(
                handle_feishu_message(event, ctx),
                main_loop,
            )

        def _sync_reaction_handler(event: P2ImMessageReactionCreatedV1) -> None:
            asyncio.run_coroutine_threadsafe(
                handle_feishu_reaction_created(event, ctx),
                main_loop,
            )

        dispatcher = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_sync_handler)
            .register_p2_im_message_reaction_created_v1(_sync_reaction_handler)
            .register_p2_im_message_reaction_deleted_v1(lambda _: None)
            .register_p2_im_message_recalled_v1(lambda _: None)
            .register_p2_im_message_message_read_v1(lambda _: None)
            .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(lambda _: None)
            .register_p2_customized_event("p2p_chat_create", lambda _: None)
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
