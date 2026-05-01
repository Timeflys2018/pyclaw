from __future__ import annotations


class FeishuDedup:
    def __init__(
        self,
        redis_client: object | None = None,
        key_prefix: str = "pyclaw:",
        ttl_seconds: int = 43200,
    ) -> None:
        self._redis = redis_client
        self._prefix = key_prefix
        self._ttl = ttl_seconds
        self._seen: dict[str, bool] = {}

    async def is_duplicate(self, message_id: str) -> bool:
        key = f"{self._prefix}feishu:dedup:{message_id}"

        if self._redis is not None:
            try:
                result = await self._redis.set(key, "1", nx=True, ex=self._ttl)  # type: ignore[attr-defined]
                return result is None
            except Exception:
                pass

        if message_id in self._seen:
            return True
        self._seen[message_id] = True
        return False
