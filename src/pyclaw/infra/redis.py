from __future__ import annotations

import logging

import redis.asyncio as aioredis

from pyclaw.infra.settings import RedisSettings

logger = logging.getLogger(__name__)


async def get_client(settings: RedisSettings) -> aioredis.Redis:
    url = settings.build_url()
    client: aioredis.Redis = aioredis.from_url(
        url,
        decode_responses=True,
        encoding="utf-8",
    )
    return client


async def close_client(client: aioredis.Redis) -> None:
    try:
        await client.aclose()
    except Exception:
        logger.exception("error closing Redis client")


async def ping(client: aioredis.Redis) -> bool:
    try:
        return await client.ping()
    except Exception:
        logger.debug("Redis ping failed", exc_info=True)
        return False
