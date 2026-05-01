from __future__ import annotations

import json
import logging

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    GetMessageResourceRequest,
    ListMessageRequest,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)
from lark_oapi.core.enum import AccessTokenType
from lark_oapi.core.model.base_request import BaseRequest

from pyclaw.infra.settings import FeishuSettings

logger = logging.getLogger(__name__)


class FeishuClient:
    def __init__(self, settings: FeishuSettings) -> None:
        self._settings = settings
        self._client = (
            lark.Client.builder()
            .app_id(settings.app_id)
            .app_secret(settings.app_secret)
            .build()
        )

    async def probe_bot_identity(self) -> str:
        req = (
            BaseRequest.builder()
            .http_method("GET")
            .uri("/open-apis/bot/v3/info")
            .token_types({AccessTokenType.TENANT})
            .build()
        )
        resp = await self._client.arequest(req)
        if not resp.success():
            raise RuntimeError(f"probe_bot_identity failed: {resp.code} {resp.msg}")
        data = json.loads(resp.raw.content)
        bot = data.get("bot", {})
        return str(bot.get("open_id", ""))

    async def reply_text(self, message_id: str, text: str) -> str | None:
        body = (
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}))
            .build()
        )
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(body)
            .build()
        )
        resp = await self._client.im.v1.message.areply(req)
        if not resp.success():
            logger.warning("reply_text failed: %s %s", resp.code, resp.msg)
            return None
        return resp.data.message_id if resp.data else None

    async def get_recent_messages(self, chat_id: str, limit: int = 20) -> list[dict]:  # type: ignore[type-arg]
        req = (
            ListMessageRequest.builder()
            .container_id_type("chat")
            .container_id(chat_id)
            .sort_type("ByCreateTimeDesc")
            .page_size(limit)
            .build()
        )
        resp = await self._client.im.v1.message.alist(req)
        if not resp.success():
            logger.warning("get_recent_messages failed: %s %s", resp.code, resp.msg)
            return []
        items = resp.data.items if resp.data and resp.data.items else []
        return [
            {
                "sender_id": getattr(getattr(item, "sender", None), "id", None),
                "msg_type": getattr(item, "msg_type", ""),
                "content": getattr(item, "body", {}).content if hasattr(getattr(item, "body", None), "content") else "",
            }
            for item in items
        ]

    async def download_image(self, message_id: str, image_key: str) -> bytes:
        req = (
            GetMessageResourceRequest.builder()
            .message_id(message_id)
            .file_key(image_key)
            .type("image")
            .build()
        )
        resp = await self._client.im.v1.message_resource.aget(req)
        if not resp.success():
            raise RuntimeError(f"download_image failed: {resp.code} {resp.msg}")
        return resp.raw.content
