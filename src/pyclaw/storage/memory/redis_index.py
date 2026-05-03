from __future__ import annotations

from typing import TYPE_CHECKING

from pyclaw.storage.memory.base import MemoryEntry

if TYPE_CHECKING:
    import redis.asyncio as aioredis


class RedisL1Index:
    def __init__(
        self,
        client: aioredis.Redis,
        *,
        key_prefix: str = "pyclaw:",
        max_entries: int = 30,
        max_chars: int = 3000,
        ttl_seconds: int = 2_592_000,
    ) -> None:
        self._client = client
        self._key_prefix = key_prefix
        self._max_entries = max_entries
        self._max_chars = max_chars
        self._ttl_seconds = ttl_seconds

    def _hash_key(self, session_key: str) -> str:
        return f"{self._key_prefix}memory:L1:{session_key}"

    async def index_get(self, session_key: str) -> list[MemoryEntry]:
        raw = await self._client.hgetall(self._hash_key(session_key))
        if not raw:
            return []
        entries = [MemoryEntry.model_validate_json(v) for v in raw.values()]
        entries.sort(key=lambda e: e.updated_at, reverse=True)
        return entries

    async def index_update(
        self, session_key: str, entry: MemoryEntry
    ) -> None:
        key = self._hash_key(session_key)
        await self._client.hset(key, entry.id, entry.model_dump_json())
        await self._evict(key)
        await self._client.expire(key, self._ttl_seconds)

    async def index_remove(
        self, session_key: str, entry_id: str
    ) -> None:
        await self._client.hdel(self._hash_key(session_key), entry_id)

    async def close(self) -> None:
        pass

    async def _evict(self, key: str) -> None:
        raw = await self._client.hgetall(key)
        if not raw:
            return
        entries = [
            (field, MemoryEntry.model_validate_json(value))
            for field, value in raw.items()
        ]
        entries.sort(key=lambda pair: pair[1].updated_at)

        while len(entries) > self._max_entries:
            field, _ = entries.pop(0)
            await self._client.hdel(key, field)

        total = sum(len(pair[1].content) for pair in entries)
        while total > self._max_chars and entries:
            field, removed = entries.pop(0)
            await self._client.hdel(key, field)
            total -= len(removed.content)
