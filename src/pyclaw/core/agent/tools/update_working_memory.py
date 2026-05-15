from __future__ import annotations

import logging
from typing import Any

from pyclaw.core.agent.tools.registry import ToolContext, error_result, text_result
from pyclaw.models import ToolResult

logger = logging.getLogger(__name__)


class UpdateWorkingMemoryTool:
    name = "update_working_memory"
    description = (
        "Write a key/value pair to short-term per-session working memory."
        " Use this to remember facts, decisions, or context within the current session."
        " Max 1024 chars total across all entries; oldest entries are evicted when exceeded."
        " Entries expire after 7 days of inactivity."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "Short identifier for the memory entry"},
            "value": {"type": "string", "description": "Value to remember"},
        },
        "required": ["key", "value"],
    }
    side_effect = True
    tool_class = "read"

    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "pyclaw:wm:",
        max_chars: int = 1024,
        ttl_seconds: int = 604800,
    ) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._max_chars = max_chars
        self._ttl_seconds = ttl_seconds

    def _hash_key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    def _order_key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}:order"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        try:
            key = args.get("key", "")
            value = args.get("value", "")
            if not key or not isinstance(key, str):
                return error_result(
                    call_id, "update_working_memory: 'key' must be a non-empty string"
                )
            if not isinstance(value, str):
                return error_result(call_id, "update_working_memory: 'value' must be a string")

            hash_key = self._hash_key(context.session_id)
            order_key = self._order_key(context.session_id)

            existing = await self._redis.hget(hash_key, key)
            await self._redis.hset(hash_key, key, value)

            if existing is None:
                await self._redis.rpush(order_key, key)

            await self._evict_if_needed(hash_key, order_key)

            await self._redis.expire(hash_key, self._ttl_seconds)
            await self._redis.expire(order_key, self._ttl_seconds)

            return text_result(call_id, f"stored '{key}' in working memory")
        except Exception as exc:
            return error_result(call_id, f"update_working_memory: {exc}")

    async def _evict_if_needed(self, hash_key: str, order_key: str) -> None:
        all_fields = await self._redis.hgetall(hash_key)
        total_chars = sum(len(k) + len(v) for k, v in all_fields.items())

        while total_chars > self._max_chars:
            oldest = await self._redis.lpop(order_key)
            if oldest is None:
                break
            if oldest in all_fields:
                total_chars -= len(oldest) + len(all_fields[oldest])
                del all_fields[oldest]
                await self._redis.hdel(hash_key, oldest)
