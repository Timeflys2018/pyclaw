from __future__ import annotations

import redis.asyncio as aioredis


class RedisWorkspaceStore:
    def __init__(self, client: aioredis.Redis, key_prefix: str = "pyclaw:") -> None:
        self._client = client
        self._prefix = key_prefix

    def _key(self, workspace_id: str, filename: str) -> str:
        return f"{self._prefix}workspace:{workspace_id}:{filename}"

    async def get_file(self, workspace_id: str, filename: str) -> str | None:
        val = await self._client.get(self._key(workspace_id, filename))
        if val is None:
            return None
        return val if isinstance(val, str) else val.decode("utf-8")

    async def put_file(self, workspace_id: str, filename: str, content: str) -> None:
        await self._client.set(self._key(workspace_id, filename), content)
