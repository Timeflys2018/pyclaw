from __future__ import annotations

from pyclaw.storage.memory.base import ArchiveEntry, MemoryEntry, MemoryStore
from pyclaw.storage.protocols import MemoryStore as MemoryStoreReExport


class _StubMemoryStore:
    async def index_get(self, session_key: str) -> list[MemoryEntry]:
        return []

    async def index_update(self, session_key: str, entry: MemoryEntry) -> None:
        pass

    async def index_remove(self, session_key: str, entry_id: str) -> None:
        pass

    async def search(
        self,
        session_key: str,
        query: str,
        *,
        layers: list[str] | None = None,
        limit: int = 10,
        per_layer_limits: dict[str, int] | None = None,
    ) -> list[MemoryEntry]:
        return []

    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        pass

    async def delete(self, session_key: str, entry_id: str) -> None:
        pass

    async def archive_session(
        self, session_key: str, session_id: str, summary: str
    ) -> None:
        pass

    async def search_archives(
        self, session_key: str, query: str, *, limit: int = 5, min_similarity: float = 0.0
    ) -> list[ArchiveEntry]:
        return []

    async def archive_entry(
        self, session_key: str, entry_id: str, *, reason: str = ""
    ) -> bool:
        return False

    async def close(self) -> None:
        pass


def test_stub_satisfies_protocol() -> None:
    store = _StubMemoryStore()
    assert isinstance(store, MemoryStore)


def test_re_export_is_same_class() -> None:
    assert MemoryStore is MemoryStoreReExport


def test_memory_entry_serialization() -> None:
    entry = MemoryEntry(
        id="e1",
        layer="L2",
        type="env_fact",
        content="Python 3.12",
        created_at=1000.0,
        updated_at=1000.0,
    )
    d = entry.model_dump()
    assert d["layer"] == "L2"
    assert d["status"] == "active"
    assert d["use_count"] == 0
    assert d["source_session_id"] is None


def test_memory_entry_l3_fields() -> None:
    entry = MemoryEntry(
        id="e2",
        layer="L3",
        type="workflow",
        content="deploy steps",
        created_at=1000.0,
        updated_at=1000.0,
        last_used_at=2000.0,
        use_count=5,
        status="stale",
    )
    assert entry.last_used_at == 2000.0
    assert entry.use_count == 5
    assert entry.status == "stale"


def test_archive_entry_serialization() -> None:
    entry = ArchiveEntry(
        id="a1",
        session_id="web:alice:s:abc",
        summary="did some work",
        created_at=1000.0,
        distance=0.15,
    )
    d = entry.model_dump()
    assert d["distance"] == 0.15
    assert d["session_id"] == "web:alice:s:abc"


def test_archive_entry_distance_none() -> None:
    entry = ArchiveEntry(
        id="a2",
        session_id="web:bob:s:def",
        summary="other work",
        created_at=2000.0,
    )
    assert entry.distance is None
