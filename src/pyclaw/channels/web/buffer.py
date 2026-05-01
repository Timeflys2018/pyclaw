from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class MessageBuffer:

    def __init__(
        self,
        redis_client: Any = None,
        max_entries: int = 1000,
        ttl_seconds: int = 300,
    ) -> None:
        self._redis = redis_client
        self._max_entries = max_entries
        self._ttl = ttl_seconds

    @property
    def available(self) -> bool:
        return self._redis is not None

    def _key(self, user_id: str) -> str:
        return f"pyclaw:ws_stream:{user_id}"

    async def publish(self, user_id: str, message: dict[str, Any]) -> str | None:
        if not self.available:
            return None
        key = self._key(user_id)
        entry_id = await self._redis.xadd(
            key, {"data": json.dumps(message)}, maxlen=self._max_entries
        )
        await self._redis.expire(key, self._ttl)
        return entry_id

    async def replay(
        self, user_id: str, last_id: str = "0-0"
    ) -> list[dict[str, Any]]:
        if not self.available:
            return []
        key = self._key(user_id)
        result = await self._redis.xread(
            {key: last_id}, count=self._max_entries
        )
        messages: list[dict[str, Any]] = []
        for _stream, entries in result or []:
            for _entry_id, fields in entries:
                try:
                    raw = (
                        fields[b"data"]
                        if isinstance(fields.get(b"data"), bytes)
                        else fields.get(b"data") or fields.get("data", "{}")
                    )
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    messages.append(json.loads(raw))
                except (json.JSONDecodeError, KeyError):
                    continue
        return messages

    async def cleanup(self, user_id: str) -> None:
        if not self.available:
            return
        await self._redis.delete(self._key(user_id))
