from __future__ import annotations

import time
from pathlib import Path

import pytest

from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend


def _entry(
    *,
    id: str,
    layer: str = "L2",
    content: str,
    type: str = "fact",
    status: str = "active",
    updated_at: float | None = None,
) -> MemoryEntry:
    now = time.time() if updated_at is None else updated_at
    return MemoryEntry(
        id=id,
        layer=layer,  # type: ignore[arg-type]
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


@pytest.fixture
async def configurable_backend(tmp_path: Path):
    async def _make(fts_min_query_chars: int = 3):
        b = SqliteMemoryBackend(tmp_path, fts_min_query_chars=fts_min_query_chars)
        return b

    return _make


# --- 3.11: FTS5 results ordered by BM25 rank ---


async def test_fts5_results_ordered_by_bm25_rank(backend: SqliteMemoryBackend) -> None:
    session = "ws:alice"
    await backend.store(session, _entry(id="e1", content="redis redis redis cache is fast"))
    await backend.store(session, _entry(id="e2", content="redis is used for caching"))
    await backend.store(session, _entry(id="e3", content="mentioning redis once here"))

    results = await backend.search(session, "redis", layers=["L2"])

    assert len(results) == 3
    for r in results:
        assert r.score is not None
        assert r.score < 0

    for i in range(len(results) - 1):
        assert results[i].score <= results[i + 1].score


# --- 3.12: per_layer_limits各层独立限制条目数, 不做全局截断 ---


async def test_per_layer_limits_independent_and_no_global_truncation(
    backend: SqliteMemoryBackend,
) -> None:
    session = "ws:alice"
    for i in range(5):
        await backend.store(session, _entry(id=f"f{i}", layer="L2", content=f"redis fact {i}"))
    for i in range(4):
        await backend.store(session, _entry(id=f"p{i}", layer="L3", content=f"redis workflow {i}"))

    results = await backend.search(
        session,
        "redis",
        layers=["L2", "L3"],
        per_layer_limits={"L2": 3, "L3": 2},
    )

    l2_results = [r for r in results if r.layer == "L2"]
    l3_results = [r for r in results if r.layer == "L3"]

    assert len(l2_results) == 3
    assert len(l3_results) == 2
    assert len(results) == 5


async def test_per_layer_limits_ignores_limit_param(
    backend: SqliteMemoryBackend,
) -> None:
    session = "ws:bob"
    for i in range(4):
        await backend.store(session, _entry(id=f"f{i}", layer="L2", content=f"python fact {i}"))
    for i in range(3):
        await backend.store(session, _entry(id=f"p{i}", layer="L3", content=f"python workflow {i}"))

    results = await backend.search(
        session,
        "python",
        layers=["L2", "L3"],
        limit=2,
        per_layer_limits={"L2": 3, "L3": 2},
    )

    assert len(results) == 5


# --- 3.13: 向后兼容 (未传 per_layer_limits 时 results[:limit] 截断) ---


async def test_backward_compat_without_per_layer_limits(
    backend: SqliteMemoryBackend,
) -> None:
    session = "ws:carol"
    for i in range(6):
        await backend.store(session, _entry(id=f"f{i}", layer="L2", content=f"docker fact {i}"))

    results = await backend.search(session, "docker", layers=["L2"], limit=3)

    assert len(results) == 3


async def test_backward_compat_both_layers_truncated(
    backend: SqliteMemoryBackend,
) -> None:
    session = "ws:dan"
    for i in range(4):
        await backend.store(session, _entry(id=f"f{i}", layer="L2", content=f"kafka fact {i}"))
    for i in range(3):
        await backend.store(session, _entry(id=f"p{i}", layer="L3", content=f"kafka workflow {i}"))

    results = await backend.search(session, "kafka", layers=["L2", "L3"], limit=5)

    assert len(results) == 5


# --- 3.14: search_archives 按 min_similarity 过滤 ---


class _StubEmbedding:
    """Stub embedding client that returns deterministic vectors based on content hash."""

    dimensions = 4

    def __init__(self):
        self._call_count = 0

    async def embed(self, text: str) -> list[float]:
        self._call_count += 1
        text_lower = text.lower()
        if "python" in text_lower:
            return [1.0, 0.0, 0.0, 0.0]
        if "rust" in text_lower:
            return [0.0, 1.0, 0.0, 0.0]
        if "go" in text_lower:
            return [0.0, 0.0, 1.0, 0.0]
        return [0.0, 0.0, 0.0, 1.0]


async def test_search_archives_filters_by_min_similarity(tmp_path: Path) -> None:
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        pytest.skip("sqlite_vec not available")

    embedding = _StubEmbedding()
    b = SqliteMemoryBackend(tmp_path, embedding=embedding)
    try:
        await b.archive_session("ws:x", "s1", "python programming tips")
        await b.archive_session("ws:x", "s2", "rust systems programming")
        await b.archive_session("ws:x", "s3", "go concurrency patterns")

        results = await b.search_archives("ws:x", "python", limit=10, min_similarity=0.9)
        assert len(results) == 1
        assert "python" in results[0].summary
        assert results[0].similarity is not None
        assert results[0].similarity >= 0.9

        results_low = await b.search_archives("ws:x", "python", limit=10, min_similarity=0.0)
        assert len(results_low) == 3
    finally:
        await b.close()


# --- 3.15: LIKE 查询结果按 updated_at 倒序 ---


async def test_like_query_ordered_by_updated_at_desc(tmp_path: Path) -> None:
    b = SqliteMemoryBackend(tmp_path, fts_min_query_chars=5)
    try:
        base_ts = time.time()
        await b.store(
            "ws:x",
            _entry(id="e1", content="abc first entry", updated_at=base_ts),
        )
        await b.store(
            "ws:x",
            _entry(id="e2", content="abc middle entry", updated_at=base_ts + 100),
        )
        await b.store(
            "ws:x",
            _entry(id="e3", content="abc latest entry", updated_at=base_ts + 200),
        )

        results = await b.search("ws:x", "abc", layers=["L2"])

        assert len(results) == 3
        assert results[0].id == "e3"
        assert results[1].id == "e2"
        assert results[2].id == "e1"
    finally:
        await b.close()


# --- 3.16: fts_min_query_chars 可配 ---


async def test_fts_min_query_chars_configurable(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d1.mkdir()
    b_default = SqliteMemoryBackend(d1)
    try:
        await b_default.store("ws:y", _entry(id="e1", content="redis"))
        results_default = await b_default.search("ws:y", "re", layers=["L2"])
        assert len(results_default) == 1
    finally:
        await b_default.close()

    d2 = tmp_path / "d2"
    d2.mkdir()
    b_strict = SqliteMemoryBackend(d2, fts_min_query_chars=5)
    try:
        base_ts = time.time()
        await b_strict.store("ws:y", _entry(id="e1", content="abcd old", updated_at=base_ts))
        await b_strict.store("ws:y", _entry(id="e2", content="abcd new", updated_at=base_ts + 100))
        results = await b_strict.search("ws:y", "abcd", layers=["L2"])
        assert len(results) == 2
        assert results[0].id == "e2"
        assert results[1].id == "e1"

        for r in results:
            assert r.score is None
    finally:
        await b_strict.close()


# --- Bonus: score field is populated for FTS5, None for LIKE ---


async def test_score_field_populated_from_fts5_rank(backend: SqliteMemoryBackend) -> None:
    session = "ws:score-test"
    await backend.store(session, _entry(id="e1", content="alpha beta gamma"))

    results = await backend.search(session, "alpha", layers=["L2"])

    assert len(results) == 1
    assert results[0].score is not None
    assert isinstance(results[0].score, float)
    assert results[0].score < 0


async def test_score_field_none_for_like_fallback(tmp_path: Path) -> None:
    b = SqliteMemoryBackend(tmp_path, fts_min_query_chars=10)
    try:
        await b.store("ws:x", _entry(id="e1", content="xyz content"))

        results = await b.search("ws:x", "xyz", layers=["L2"])

        assert len(results) == 1
        assert results[0].score is None
    finally:
        await b.close()


# --- Bonus: ArchiveEntry.similarity field populated ---


async def test_archive_entry_similarity_populated(tmp_path: Path) -> None:
    try:
        import sqlite_vec  # noqa: F401
    except ImportError:
        pytest.skip("sqlite_vec not available")

    b = SqliteMemoryBackend(tmp_path, embedding=_StubEmbedding())
    try:
        await b.archive_session("ws:z", "s1", "python is cool")

        results = await b.search_archives("ws:z", "python", limit=5)

        assert len(results) == 1
        assert results[0].similarity is not None
        assert results[0].distance is not None
        assert abs(results[0].similarity - (1.0 - results[0].distance)) < 1e-6
    finally:
        await b.close()
