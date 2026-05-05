from __future__ import annotations

import logging
from typing import Any

from pyclaw.core.hooks import (
    CompactionContext,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)
from pyclaw.models import CompactResult

logger = logging.getLogger(__name__)


class WorkingMemoryHook:
    """Implements AgentHook Protocol.

    before_prompt_build: HGETALL pyclaw:wm:{session_id} → format as <working_memory> XML block.
    """

    def __init__(
        self,
        redis_client: Any,
        *,
        key_prefix: str = "pyclaw:wm:",
        max_chars: int = 1024,
    ) -> None:
        self._redis = redis_client
        self._key_prefix = key_prefix
        self._max_chars = max_chars

    def _hash_key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}"

    def _order_key(self, session_id: str) -> str:
        return f"{self._key_prefix}{session_id}:order"

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        try:
            hash_key = self._hash_key(context.session_id)
            order_key = self._order_key(context.session_id)

            all_fields = await self._redis.hgetall(hash_key)
            if not all_fields:
                return None

            order = await self._redis.lrange(order_key, 0, -1)
            if order:
                ordered_keys = [k for k in order if k in all_fields]
                remaining = [k for k in all_fields if k not in set(order)]
                ordered_keys.extend(remaining)
            else:
                ordered_keys = list(all_fields.keys())

            lines: list[str] = []
            for k in ordered_keys:
                lines.append(f"- {k}: {all_fields[k]}")

            block = "<working_memory>\n" + "\n".join(lines) + "\n</working_memory>"

            while len(block) > self._max_chars and lines:
                lines.pop(0)
                if not lines:
                    return None
                block = "<working_memory>\n" + "\n".join(lines) + "\n</working_memory>"

            return PromptBuildResult(append=block)
        except Exception as exc:
            logger.warning("WorkingMemoryHook.before_prompt_build failed: %s", exc)
            return None

    async def after_response(self, observation: ResponseObservation) -> None:
        return None

    async def before_compaction(self, context: CompactionContext) -> None:
        return None

    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None:
        return None
