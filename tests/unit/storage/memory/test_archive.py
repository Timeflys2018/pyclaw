from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

sqlite_vec = pytest.importorskip("sqlite_vec")

from pyclaw.storage.memory.base import ArchiveEntry  # noqa: E402
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend  # noqa: E402


def _make_embedding_client(dimensions: int = 4) -> MagicMock:
    client = MagicMock()
    client.dimensions = dimensions
    client.embed = AsyncMock(return_value=[0.1, 0.2, 0.3, 0.4])
    client.embed_batch = AsyncMock(return_value=[[0.1, 0.2, 0.3, 0.4]])
    return client


def _query_sync(backend: SqliteMemoryBackend, session_key: str, sql: str) -> list[dict]:
    conn = backend._get_conn_sync(session_key)
    cursor = conn.execute(sql)
    desc = cursor.getdescription()
    return [{d[0]: v for d, v in zip(desc, row)} for row in cursor]


async def test_archive_session_writes_to_both_tables(tmp_path: Path) -> None:
    client = _make_embedding_client()
    b = SqliteMemoryBackend(tmp_path, client)
    try:
        await b.archive_session("ws:alice", "sess-001", "User discussed Python testing")

        rows = _query_sync(b, "ws:alice", "SELECT * FROM archives")
        assert len(rows) == 1
        assert rows[0]["session_id"] == "sess-001"
        assert rows[0]["summary"] == "User discussed Python testing"

        vec_rows = _query_sync(b, "ws:alice", "SELECT count(*) as cnt FROM archives_vec")
        assert vec_rows[0]["cnt"] == 1

        client.embed.assert_awaited_once_with("User discussed Python testing")
    finally:
        await b.close()


async def test_search_archives_returns_nearest_results(tmp_path: Path) -> None:
    client = _make_embedding_client()
    call_count = 0

    async def varying_embed(text: str) -> list[float]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return [0.1, 0.2, 0.3, 0.4]
        elif call_count == 2:
            return [0.9, 0.8, 0.7, 0.6]
        else:
            return [0.1, 0.2, 0.3, 0.4]

    client.embed = AsyncMock(side_effect=varying_embed)
    b = SqliteMemoryBackend(tmp_path, client)
    try:
        await b.archive_session("ws:alice", "sess-001", "Python testing discussion")
        await b.archive_session("ws:alice", "sess-002", "Rust performance analysis")

        results = await b.search_archives("ws:alice", "Python testing")
        assert len(results) == 2
        assert all(isinstance(r, ArchiveEntry) for r in results)
        assert results[0].distance is not None
    finally:
        await b.close()


async def test_embedding_failure_on_archive_still_writes_summary(tmp_path: Path) -> None:
    client = _make_embedding_client()
    client.embed = AsyncMock(side_effect=RuntimeError("API down"))
    b = SqliteMemoryBackend(tmp_path, client)
    try:
        await b.archive_session("ws:alice", "sess-001", "Important session summary")

        rows = _query_sync(b, "ws:alice", "SELECT * FROM archives")
        assert len(rows) == 1
        assert rows[0]["summary"] == "Important session summary"

        vec_rows = _query_sync(b, "ws:alice", "SELECT count(*) as cnt FROM archives_vec")
        assert vec_rows[0]["cnt"] == 0
    finally:
        await b.close()


async def test_embedding_failure_on_search_returns_empty(tmp_path: Path) -> None:
    client = _make_embedding_client()
    b = SqliteMemoryBackend(tmp_path, client)
    try:
        await b.archive_session("ws:alice", "sess-001", "Some summary")
        client.embed = AsyncMock(side_effect=RuntimeError("API down"))

        results = await b.search_archives("ws:alice", "anything")
        assert results == []
    finally:
        await b.close()


async def test_search_empty_archives_returns_empty(tmp_path: Path) -> None:
    client = _make_embedding_client()
    b = SqliteMemoryBackend(tmp_path, client)
    try:
        results = await b.search_archives("ws:alice", "nonexistent")
        assert results == []
    finally:
        await b.close()


async def test_no_embedding_client_returns_empty(tmp_path: Path) -> None:
    b = SqliteMemoryBackend(tmp_path)
    try:
        await b.archive_session("ws:alice", "sess-001", "summary")
        results = await b.search_archives("ws:alice", "anything")
        assert results == []

        rows = _query_sync(b, "ws:alice", "SELECT * FROM archives")
        assert len(rows) == 1
    finally:
        await b.close()
