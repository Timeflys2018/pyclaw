"""CuratorStateStore: sole writer of curator cross-instance Redis keys.

Invariant: no other code in the codebase should write to the keys owned by
this class. Reads are permitted via ``get_last_*`` methods. Legacy
constants (``CURATOR_LAST_RUN_KEY`` / ``CURATOR_LLM_REVIEW_KEY`` in
``pyclaw.core.curator``) are kept as deprecated re-exports for test
back-compat; the canonical write path is this class.

Serialization format (preserved from legacy code for wire compatibility):
    last_run_at:            ``str(time.time())`` -> float-parseable string
    llm_review_last_run_at: ``str(int(time.time()))`` -> int-parseable string
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class CuratorStateStore:
    """Owns curator cross-instance Redis keys.

    Writers:
        * :meth:`mark_scan_completed` — scan phase success timestamp.
        * :meth:`mark_review_fully_completed` — LLM-review full-traversal
          success timestamp. Caller must ensure all db files were attempted
          without ``LockLostError`` before invoking this.
        * :meth:`seed_if_missing` — initialize ``last_run_at`` on first startup.

    Readers:
        * :meth:`get_last_scan_at` — float timestamp or ``None``.
        * :meth:`get_last_review_at` — int timestamp or ``None``.
    """

    _LAST_RUN_KEY: Final = "pyclaw:curator:last_run_at"
    _LLM_REVIEW_KEY: Final = "pyclaw:curator:llm_review_last_run_at"

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    async def mark_scan_completed(self) -> None:
        await self._redis.set(self._LAST_RUN_KEY, str(time.time()))

    async def mark_review_fully_completed(self) -> None:
        await self._redis.set(self._LLM_REVIEW_KEY, str(int(time.time())))

    async def seed_if_missing(self) -> None:
        """Initialize ``last_run_at`` to current time iff key is absent (NX)."""
        await self._redis.set(self._LAST_RUN_KEY, str(time.time()), nx=True)

    async def get_last_scan_at(self) -> float | None:
        raw = await self._redis.get(self._LAST_RUN_KEY)
        return _parse_float(raw)

    async def get_last_review_at(self) -> int | None:
        raw = await self._redis.get(self._LLM_REVIEW_KEY)
        parsed = _parse_float(raw)
        if parsed is None:
            return None
        return int(parsed)


def _parse_float(raw: object) -> float | None:
    """Decode a Redis value into a float, tolerating bytes/str/None/garbage."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    elif isinstance(raw, str):
        text = raw
    else:
        return None
    try:
        return float(text)
    except ValueError:
        return None
