from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""


class AffinityRegistry:
    def __init__(
        self,
        redis_client: Any,
        worker_id: str,
        *,
        key_prefix: str = "pyclaw:",
        ttl_seconds: int = 300,
    ) -> None:
        self._redis = redis_client
        self._worker_id = worker_id
        self._prefix = key_prefix
        self._ttl = ttl_seconds

    @property
    def worker_id(self) -> str:
        return self._worker_id

    @property
    def ttl_seconds(self) -> int:
        return self._ttl

    def _key(self, session_key: str) -> str:
        return f"{self._prefix}affinity:{session_key}"

    async def resolve(self, session_key: str) -> str | None:
        raw = await self._redis.get(self._key(session_key))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode()
        return str(raw)

    async def claim(self, session_key: str) -> bool:
        result = await self._redis.set(
            self._key(session_key),
            self._worker_id,
            nx=True,
            ex=self._ttl,
        )
        return bool(result)

    async def renew(self, session_key: str) -> None:
        await self._redis.expire(self._key(session_key), self._ttl)

    async def release(self, session_key: str) -> bool:
        result = await self._redis.eval(_RELEASE_SCRIPT, 1, self._key(session_key), self._worker_id)
        return bool(result)

    async def force_claim(self, session_key: str) -> None:
        await self._redis.set(
            self._key(session_key),
            self._worker_id,
            ex=self._ttl,
        )

    def is_mine(self, owner_id: str | None) -> bool:
        return owner_id == self._worker_id
