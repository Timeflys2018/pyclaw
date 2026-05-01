from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import lark_oapi as lark
from lark_oapi import EventDispatcherHandler
from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1

from pyclaw.channels.feishu.client import FeishuClient
from pyclaw.channels.feishu.dedup import FeishuDedup
from pyclaw.channels.feishu.handler import FeishuContext, handle_feishu_message
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
    ) -> None:
        self._settings = settings
        self._feishu_client = feishu_client
        self._deps = deps
        self._dedup = dedup
        self._workspace_store = workspace_store
        self._ws_client: lark.ws.Client | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._status: str = "disconnected"

    @property
    def status(self) -> str:
        return self._status

    async def start(self) -> None:
        bot_open_id = await self._feishu_client.probe_bot_identity()
        logger.info("Feishu bot open_id: %s", bot_open_id)

        ctx = FeishuContext(
            settings=self._settings,
            feishu_client=self._feishu_client,
            deps=self._deps,
            dedup=self._dedup,
            workspace_store=self._workspace_store,
            bot_open_id=bot_open_id,
            workspace_base=Path.home() / ".pyclaw/workspaces",
        )

        loop = asyncio.get_event_loop()
        self._loop = loop

        def _sync_handler(event: P2ImMessageReceiveV1) -> None:
            asyncio.run_coroutine_threadsafe(
                handle_feishu_message(event, ctx),
                loop,
            )

        dispatcher = (
            EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(_sync_handler)
            .build()
        )

        self._ws_client = lark.ws.Client(
            self._settings.app_id,
            self._settings.app_secret,
            event_handler=dispatcher,
            auto_reconnect=True,
        )

        self._status = "connected"
        await loop.run_in_executor(None, self._ws_client.start)

    async def stop(self) -> None:
        self._status = "disconnected"
        logger.info("Feishu channel stopped")
