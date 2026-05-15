from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.infra.redis import close_client, ping


@pytest.mark.asyncio
async def test_ping_returns_true_on_connected_mock() -> None:
    client = AsyncMock()
    client.ping = AsyncMock(return_value=True)
    assert await ping(client) is True


@pytest.mark.asyncio
async def test_ping_returns_false_on_connection_error() -> None:
    client = AsyncMock()
    client.ping = AsyncMock(side_effect=ConnectionError("refused"))
    assert await ping(client) is False


@pytest.mark.asyncio
async def test_close_client_calls_aclose() -> None:
    client = AsyncMock()
    client.aclose = AsyncMock()
    await close_client(client)
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_client_swallows_exception() -> None:
    client = AsyncMock()
    client.aclose = AsyncMock(side_effect=RuntimeError("already closed"))
    await close_client(client)
