"""SopCandidateTracker — collects per-turn metadata for self-evolution SOP extraction."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from pyclaw.core.hooks import (
    CompactionContext,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)
from pyclaw.infra.settings import EvolutionSettings
from pyclaw.models import CompactResult

logger = logging.getLogger(__name__)


class SopCandidateTracker:
    """Records candidate turn metadata to Redis for later SOP extraction.

    On each turn with tool_calls, stores a candidate dict in Redis hash
    ``pyclaw:sop_candidates:{session_id}`` keyed by turn_id (the assistant
    message id). Stays no-op when the feature is disabled.

    FIFO-evicts oldest candidates when count exceeds ``max_candidates`` to
    prevent unbounded Redis hash growth.
    """

    KEY_PREFIX = "pyclaw:sop_candidates:"
    EVICTION_BATCH = 10  # When over cap, evict this many oldest at once

    def __init__(
        self,
        redis_client: Any,  # noqa: ANN401
        settings: EvolutionSettings,
        *,
        task_manager: Any = None,  # noqa: ANN401
        memory_store: Any = None,  # noqa: ANN401
        session_store: Any = None,  # noqa: ANN401
        llm_client: Any = None,  # noqa: ANN401
        nudge_hook: Any = None,  # noqa: ANN401
    ) -> None:
        self._redis = redis_client
        self._settings = settings
        self._task_manager = task_manager
        self._memory_store = memory_store
        self._session_store = session_store
        self._llm_client = llm_client
        self._nudge_hook = nudge_hook
        self._last_user_msg: dict[str, str] = {}

    @staticmethod
    def _key(session_id: str) -> str:
        return f"{SopCandidateTracker.KEY_PREFIX}{session_id}"

    def cleanup_session(self, session_id: str) -> None:
        """Remove per-session in-memory state to prevent unbounded growth.

        Called from after_compaction to release `_last_user_msg` entry
        for sessions that have ended/compacted.
        """
        self._last_user_msg.pop(session_id, None)
        logger.debug("cleaned up tracker state for session %s", session_id)

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        """Capture the user prompt for the matching after_response call."""
        if not self._settings.enabled:
            return None
        if context.prompt:
            self._last_user_msg[context.session_id] = context.prompt[:200]
        return None

    async def after_response(self, observation: ResponseObservation) -> None:
        """If turn had tool_calls, record a candidate to Redis."""
        if not self._settings.enabled:
            return None
        if not observation.tool_calls:
            return None

        session_id = observation.session_id
        # Extract tool function names
        tool_names: list[str] = []
        for call in observation.tool_calls:
            fn = (call or {}).get("function") or {}
            name = fn.get("name", "") if isinstance(fn, dict) else ""
            if name:
                tool_names.append(name)

        # turn_id: prefer first tool_call's id (stable across the turn);
        # fall back to a timestamp-based synthetic id
        turn_id = ""
        first_call = observation.tool_calls[0] if observation.tool_calls else None
        if isinstance(first_call, dict):
            turn_id = str(first_call.get("id", "") or "")
        if not turn_id:
            turn_id = f"turn_{time.time():.6f}"

        candidate = {
            "turn_id": turn_id,
            "user_msg": self._last_user_msg.get(session_id, "")[:200],
            "tool_names": tool_names,
            "timestamp": time.time(),
        }

        try:
            key = self._key(session_id)
            await self._redis.hset(key, turn_id, json.dumps(candidate))
            await self._maybe_evict(key)
        except Exception:
            # Never let tracker failures break the agent loop
            logger.warning(
                "SopCandidateTracker.after_response failed for session %s",
                session_id,
                exc_info=True,
            )
        return None

    async def _maybe_evict(self, key: str) -> None:
        """FIFO-evict oldest candidates when over max_candidates cap."""
        try:
            count = await self._redis.hlen(key)
        except Exception:
            return
        if count <= self._settings.max_candidates:
            return

        # Read all candidates, sort by timestamp, delete oldest EVICTION_BATCH
        try:
            all_entries = await self._redis.hgetall(key)
        except Exception:
            return

        items: list[tuple[str, float]] = []
        for field_name, value in all_entries.items():
            field_str = field_name.decode() if isinstance(field_name, bytes) else field_name
            value_str = value.decode() if isinstance(value, bytes) else value
            try:
                parsed = json.loads(value_str)
                ts = float(parsed.get("timestamp", 0))
            except (json.JSONDecodeError, ValueError, TypeError):
                ts = 0
            items.append((field_str, ts))

        # Oldest first
        items.sort(key=lambda x: x[1])
        to_delete = [field for field, _ in items[: self.EVICTION_BATCH]]
        if to_delete:
            try:
                await self._redis.hdel(key, *to_delete)
            except Exception:
                logger.warning(
                    "SopCandidateTracker eviction failed for key %s",
                    key,
                    exc_info=True,
                )

    async def before_compaction(self, context: CompactionContext) -> None:
        return None

    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None:
        try:
            if not self._settings.enabled:
                return None
            if (
                self._task_manager is None
                or self._memory_store is None
                or self._session_store is None
                or self._llm_client is None
            ):
                return None
            if not getattr(result, "compacted", False):
                return None

            try:
                from pyclaw.core.sop_extraction import maybe_spawn_extraction

                await maybe_spawn_extraction(
                    task_manager=self._task_manager,
                    memory_store=self._memory_store,
                    session_store=self._session_store,
                    redis_client=self._redis,
                    llm_client=self._llm_client,
                    session_id=context.session_id,
                    settings=self._settings,
                    nudge_hook=self._nudge_hook,
                )
            except Exception:
                logger.warning(
                    "sop after_compaction trigger failed for %s",
                    context.session_id,
                    exc_info=True,
                )
            return None
        finally:
            self.cleanup_session(context.session_id)
