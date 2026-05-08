from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.channels.feishu.streaming import FeishuStreamingCard


def _make_mock_client(card_id: str = "card_123") -> Any:
    mock_resp = MagicMock()
    mock_resp.success.return_value = True
    mock_resp.data = MagicMock()
    mock_resp.data.card_id = card_id

    reply_resp = MagicMock()
    reply_resp.success.return_value = True

    content_resp = MagicMock()
    content_resp.success.return_value = True

    settings_resp = MagicMock()
    settings_resp.success.return_value = True

    client = MagicMock()
    client.cardkit.v1.card.acreate = AsyncMock(return_value=mock_resp)
    client.cardkit.v1.card.asettings = AsyncMock(return_value=settings_resp)
    client.cardkit.v1.card_element.acontent = AsyncMock(return_value=content_resp)
    client.im.v1.message.areply = AsyncMock(return_value=reply_resp)
    return client


@pytest.mark.asyncio
async def test_streaming_card_throttles_updates() -> None:
    client = _make_mock_client()
    card = FeishuStreamingCard(client, "msg_001")
    await card.start()

    update_count = 0
    original_last = 0.0

    for i in range(5):
        await card.update(f"text {i}")
        if client.cardkit.v1.card_element.acontent.await_count > update_count:
            update_count = client.cardkit.v1.card_element.acontent.await_count

    assert client.cardkit.v1.card_element.acontent.await_count <= 5


@pytest.mark.asyncio
async def test_streaming_card_finish_closes_mode() -> None:
    client = _make_mock_client()
    card = FeishuStreamingCard(client, "msg_002")
    await card.start()
    await card.finish("final text")

    client.cardkit.v1.card.asettings.assert_awaited_once()


@pytest.mark.asyncio
async def test_streaming_card_falls_back_on_creation_error() -> None:
    fail_resp = MagicMock()
    fail_resp.success.return_value = False
    fail_resp.code = 500
    fail_resp.msg = "internal error"

    client = MagicMock()
    client.cardkit.v1.card.acreate = AsyncMock(return_value=fail_resp)

    card = FeishuStreamingCard(client, "msg_003")
    with pytest.raises(RuntimeError):
        await card.start()
