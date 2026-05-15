from __future__ import annotations

from typing import Literal, Protocol, runtime_checkable

from pydantic import BaseModel

MemoryLayer = Literal["L1", "L2", "L3"]


class MemoryEntry(BaseModel):
    id: str
    layer: MemoryLayer
    type: str
    content: str
    source_session_id: str | None = None
    created_at: float
    updated_at: float
    last_used_at: float | None = None
    use_count: int = 0
    status: str = "active"
    score: float | None = None
    low_confidence: bool = False


class ArchiveEntry(BaseModel):
    id: str
    session_id: str
    summary: str
    created_at: float
    distance: float | None = None
    similarity: float | None = None
    low_confidence: bool = False


@runtime_checkable
class MemoryStore(Protocol):
    async def index_get(self, session_key: str) -> list[MemoryEntry]: ...
    async def index_update(self, session_key: str, entry: MemoryEntry) -> None: ...
    async def index_remove(self, session_key: str, entry_id: str) -> None: ...

    async def search(
        self,
        session_key: str,
        query: str,
        *,
        layers: list[str] | None = None,
        limit: int = 10,
        per_layer_limits: dict[str, int] | None = None,
    ) -> list[MemoryEntry]: ...
    async def store(self, session_key: str, entry: MemoryEntry) -> None: ...
    async def delete(self, session_key: str, entry_id: str) -> None: ...

    async def archive_entry(self, session_key: str, entry_id: str, *, reason: str = "") -> bool: ...

    async def archive_session(self, session_key: str, session_id: str, summary: str) -> None: ...
    async def search_archives(
        self, session_key: str, query: str, *, limit: int = 5, min_similarity: float = 0.0
    ) -> list[ArchiveEntry]: ...

    async def close(self) -> None: ...
