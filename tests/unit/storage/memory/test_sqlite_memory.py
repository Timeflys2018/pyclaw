from __future__ import annotations

import time
from pathlib import Path

import pytest

from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend


def _make_entry(
    *,
    id: str = "e1",
    layer: str = "L2",
    type: str = "env_fact",
    content: str = "Python 3.12 is installed",
    status: str = "active",
) -> MemoryEntry:
    now = time.time()
    return MemoryEntry(
        id=id,
        layer=layer,
        type=type,
        content=content,
        created_at=now,
        updated_at=now,
        status=status,
    )


@pytest.fixture
async def backend(tmp_path: Path) -> SqliteMemoryBackend:
    b = SqliteMemoryBackend(tmp_path)
    yield b  # type: ignore[misc]
    await b.close()


async def test_store_l2_fact_and_search(backend: SqliteMemoryBackend) -> None:
    entry = _make_entry(content="Python 3.12 is installed")
    await backend.store("ws:alice", entry)

    results = await backend.search("ws:alice", "Python", layers=["L2"])
    assert len(results) == 1
    assert results[0].id == "e1"
    assert results[0].content == "Python 3.12 is installed"


async def test_store_l3_procedure_and_search(backend: SqliteMemoryBackend) -> None:
    entry = _make_entry(
        id="p1", layer="L3", type="workflow", content="deploy with docker compose up"
    )
    await backend.store("ws:alice", entry)

    results = await backend.search("ws:alice", "docker", layers=["L3"])
    assert len(results) == 1
    assert results[0].id == "p1"


async def test_l3_search_excludes_archived(backend: SqliteMemoryBackend) -> None:
    active = _make_entry(id="p1", layer="L3", type="workflow", content="active procedure step")
    archived = _make_entry(
        id="p2", layer="L3", type="workflow", content="archived procedure step", status="archived"
    )
    await backend.store("ws:alice", active)
    await backend.store("ws:alice", archived)

    results = await backend.search("ws:alice", "procedure", layers=["L3"])
    assert len(results) == 1
    assert results[0].id == "p1"


async def test_cross_layer_search(backend: SqliteMemoryBackend) -> None:
    fact = _make_entry(id="f1", layer="L2", content="server runs on port 8080")
    proc = _make_entry(id="p1", layer="L3", type="workflow", content="restart server on port 8080")
    await backend.store("ws:alice", fact)
    await backend.store("ws:alice", proc)

    results = await backend.search("ws:alice", "port 8080")
    assert len(results) == 2
    ids = {r.id for r in results}
    assert ids == {"f1", "p1"}


async def test_cjk_short_query_uses_like_fallback(backend: SqliteMemoryBackend) -> None:
    entry = _make_entry(id="f1", layer="L2", content="服务器运行在端口8080")
    await backend.store("ws:alice", entry)

    results = await backend.search("ws:alice", "服务", layers=["L2"])
    assert len(results) == 1
    assert results[0].id == "f1"


async def test_latin_query_uses_fts5(backend: SqliteMemoryBackend) -> None:
    entry = _make_entry(id="f1", layer="L2", content="the quick brown fox jumps")
    await backend.store("ws:alice", entry)

    results = await backend.search("ws:alice", "quick brown", layers=["L2"])
    assert len(results) == 1


async def test_delete_removes_entry(backend: SqliteMemoryBackend) -> None:
    entry = _make_entry(id="f1", layer="L2", content="temporary fact to delete")
    await backend.store("ws:alice", entry)

    await backend.delete("ws:alice", "f1")

    results = await backend.search("ws:alice", "temporary")
    assert len(results) == 0


async def test_session_key_isolation(backend: SqliteMemoryBackend) -> None:
    entry = _make_entry(id="f1", layer="L2", content="secret data for alice")
    await backend.store("ws:alice", entry)

    results = await backend.search("ws:bob", "secret")
    assert len(results) == 0


async def test_connection_reuse(backend: SqliteMemoryBackend) -> None:
    entry1 = _make_entry(id="f1", content="first entry")
    entry2 = _make_entry(id="f2", content="second entry")
    await backend.store("ws:alice", entry1)
    await backend.store("ws:alice", entry2)

    assert len(backend._connections) == 1


async def test_close_releases_connections(backend: SqliteMemoryBackend) -> None:
    await backend.store("ws:alice", _make_entry(id="f1", content="data"))
    await backend.store("ws:bob", _make_entry(id="f2", content="data"))
    assert len(backend._connections) == 2

    await backend.close()
    assert len(backend._connections) == 0
