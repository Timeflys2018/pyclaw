from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING

from lark_oapi.api.cardkit.v1.model import (
    ContentCardElementRequest,
    ContentCardElementRequestBody,
    CreateCardRequest,
    CreateCardRequestBody,
    SettingsCardRequest,
    SettingsCardRequestBody,
)
from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

STREAMING_ELEMENT_ID = "streaming_content"
THROTTLE_MS = 160


class FeishuStreamingCard:
    def __init__(self, lark_client: object, reply_to_message_id: str) -> None:
        self._client = lark_client  # type: ignore[misc]
        self._reply_to = reply_to_message_id
        self._card_id: str | None = None
        self._seq = 1
        self._last_update: float = 0.0

    async def start(self) -> None:
        card_body = json.dumps(self._build_initial_card())
        body = (
            CreateCardRequestBody.builder()
            .type("card")
            .data(card_body)
            .build()
        )
        req = CreateCardRequest.builder().request_body(body).build()
        resp = await self._client.cardkit.v1.card.acreate(req)  # type: ignore[attr-defined]
        if not resp.success():
            raise RuntimeError(f"Failed to create card: {resp.code} {resp.msg}")
        self._card_id = resp.data.card_id if resp.data else None
        if not self._card_id:
            raise RuntimeError("Card created but no card_id returned")

        reply_body = (
            ReplyMessageRequestBody.builder()
            .msg_type("interactive")
            .content(json.dumps({"type": "card", "data": {"card_id": self._card_id}}))
            .uuid(str(uuid.uuid4()))
            .build()
        )
        reply_req = (
            ReplyMessageRequest.builder()
            .message_id(self._reply_to)
            .request_body(reply_body)
            .build()
        )
        reply_resp = await self._client.im.v1.message.areply(reply_req)  # type: ignore[attr-defined]
        if not reply_resp.success():
            logger.warning("Failed to send card reply: %s %s", reply_resp.code, reply_resp.msg)

    async def update(self, text: str) -> None:
        if not self._card_id:
            return
        now = time.monotonic() * 1000
        if now - self._last_update < THROTTLE_MS:
            return
        self._last_update = now
        await self._send_content_update(text)

    async def finish(self, final_text: str) -> None:
        if not self._card_id:
            return
        await self._send_content_update(final_text)
        await self._close_streaming()

    async def error(self, message: str) -> None:
        if not self._card_id:
            return
        await self._send_content_update(f"❌ {message}")
        await self._close_streaming()

    async def _send_content_update(self, text: str) -> None:
        body = (
            ContentCardElementRequestBody.builder()
            .content(text)
            .sequence(self._seq)
            .build()
        )
        self._seq += 1
        req = (
            ContentCardElementRequest.builder()
            .card_id(self._card_id)  # type: ignore[arg-type]
            .element_id(STREAMING_ELEMENT_ID)
            .request_body(body)
            .build()
        )
        resp = await self._client.cardkit.v1.card_element.acontent(req)  # type: ignore[attr-defined]
        if not resp.success():
            logger.warning("content update failed: %s %s", resp.code, resp.msg)

    async def _close_streaming(self) -> None:
        settings_data = json.dumps({"streaming_mode": False})
        body = (
            SettingsCardRequestBody.builder()
            .settings(settings_data)
            .build()
        )
        req = (
            SettingsCardRequest.builder()
            .card_id(self._card_id)  # type: ignore[arg-type]
            .request_body(body)
            .build()
        )
        resp = await self._client.cardkit.v1.card.asettings(req)  # type: ignore[attr-defined]
        if not resp.success():
            logger.warning("close streaming failed: %s %s", resp.code, resp.msg)

    def _build_initial_card(self) -> dict:  # type: ignore[type-arg]
        return {
            "schema": "2.0",
            "streaming_mode": True,
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "element_id": STREAMING_ELEMENT_ID,
                        "content": "...",
                    }
                ]
            },
        }
