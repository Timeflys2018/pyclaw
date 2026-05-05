from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pyclaw.storage.memory.base import ArchiveEntry, MemoryEntry

if TYPE_CHECKING:
    from pyclaw.storage.memory.redis_index import RedisL1Index
    from pyclaw.storage.memory.sqlite import SqliteMemoryBackend

logger = logging.getLogger(__name__)


class CompositeMemoryStore:
    def __init__(
        self,
        *,
        l1: RedisL1Index,
        sqlite: SqliteMemoryBackend,
    ) -> None:
        self._l1 = l1
        self._sqlite = sqlite

    async def index_get(self, session_key: str) -> list[MemoryEntry]:
        return await self._l1.index_get(session_key)

    async def index_update(self, session_key: str, entry: MemoryEntry) -> None:
        await self._l1.index_update(session_key, entry)

    async def index_remove(self, session_key: str, entry_id: str) -> None:
        await self._l1.index_remove(session_key, entry_id)

    async def search(
        self,
        session_key: str,
        query: str,
        *,
        layers: list[str] | None = None,
        limit: int = 10,
        per_layer_limits: dict[str, int] | None = None,
    ) -> list[MemoryEntry]:
        return await self._sqlite.search(
            session_key, query, layers=layers, limit=limit, per_layer_limits=per_layer_limits
        )

    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        await self._sqlite.store(session_key, entry)
        l1_entry = MemoryEntry(
            id=entry.id,
            layer="L1",
            type=entry.type,
            content=entry.content[:100],
            source_session_id=entry.source_session_id,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            last_used_at=entry.last_used_at,
            use_count=entry.use_count,
            status=entry.status,
        )
        await self._l1.index_update(session_key, l1_entry)

    async def delete(self, session_key: str, entry_id: str) -> None:
        await self._sqlite.delete(session_key, entry_id)
        await self._l1.index_remove(session_key, entry_id)

    async def archive_session(
        self, session_key: str, session_id: str, summary: str
    ) -> None:
        await self._sqlite.archive_session(session_key, session_id, summary)

    async def search_archives(
        self, session_key: str, query: str, *, limit: int = 5, min_similarity: float = 0.0
    ) -> list[ArchiveEntry]:
        return await self._sqlite.search_archives(
            session_key, query, limit=limit, min_similarity=min_similarity
        )

    async def close(self) -> None:
        for backend in (self._l1, self._sqlite):
            try:
                await backend.close()
            except Exception:
                logger.warning("error closing %s", type(backend).__name__, exc_info=True)
