from __future__ import annotations

import time
from pathlib import Path

import pytest

from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend


def _entry(
    *, id: str, layer: str = "L2", content: str, type: str = "fact",
    status: str = "active", updated_at: float | None = None,
) -> MemoryEntry:
    now = time.time() if updated_at is None else updated_at
    return MemoryEntry(
        id=id, layer=layer, type=type, content=content,
        created_at=now, updated_at=now, status=status,
    )


@pytest.fixture
async def backend(tmp_path: Path) -> SqliteMemoryBackend:
    b = SqliteMemoryBackend(tmp_path)
    yield b  # type: ignore[misc]
    await b.close()


async def test_new_db_uses_jieba_tokenizer(backend: SqliteMemoryBackend) -> None:
    await backend.store("ws:x", _entry(id="e1", content="项目部署在K8s集群"))
    results = await backend.search("ws:x", "我该怎么部署项目", layers=["L2"])
    assert len(results) >= 1
    assert any("部署" in r.content for r in results)


async def test_old_trigram_db_migrated(tmp_path: Path) -> None:
    import apsw

    db_path = tmp_path / "ws_migrate.db"
    conn = apsw.Connection(str(db_path))
    conn.execute("CREATE TABLE facts (id TEXT PRIMARY KEY, session_key TEXT, type TEXT, content TEXT, source_session_id TEXT, created_at REAL, updated_at REAL)")
    conn.execute("CREATE INDEX idx_facts_type ON facts(type)")
    conn.execute("CREATE VIRTUAL TABLE facts_fts USING fts5(content, content=facts, content_rowid=rowid, tokenize='trigram')")
    conn.execute("INSERT INTO facts VALUES ('e1', 'ws:migrate', 'fact', 'Python 项目配置', NULL, 0, 0)")
    conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
    conn.close()

    b = SqliteMemoryBackend(tmp_path)
    try:
        results = await b.search("ws_migrate", "Python 项目", layers=["L2"])
        assert len(results) >= 1
    finally:
        await b.close()


async def test_chinese_natural_language_query_hits(backend: SqliteMemoryBackend) -> None:
    sk = "ws:nlq"
    await backend.store(sk, _entry(id="f1", content="项目部署在 K8s 集群"))
    await backend.store(sk, _entry(id="f2", content="Redis 连接超时需要排查"))
    await backend.store(sk, _entry(id="f3", content="我喜欢简洁的回答"))
    await backend.store(sk, _entry(id="p1", layer="L3", content="部署流程: git tag → push → CI"))

    results = await backend.search(sk, "我该怎么部署项目到生产环境", layers=["L2", "L3"])
    contents = [r.content for r in results]
    assert any("部署" in c for c in contents)

    results2 = await backend.search(sk, "Redis超时怎么修复", layers=["L2"])
    assert len(results2) >= 1
    assert any("Redis" in r.content for r in results2)


async def test_per_layer_limits_with_jieba(backend: SqliteMemoryBackend) -> None:
    sk = "ws:quota"
    for i in range(5):
        await backend.store(sk, _entry(id=f"f{i}", layer="L2", content=f"关于部署的第{i}条记录"))
    for i in range(4):
        await backend.store(sk, _entry(id=f"p{i}", layer="L3", content=f"部署相关流程{i}"))

    results = await backend.search(
        sk, "部署", layers=["L2", "L3"],
        per_layer_limits={"L2": 3, "L3": 2},
    )
    l2 = [r for r in results if r.layer == "L2"]
    l3 = [r for r in results if r.layer == "L3"]
    assert len(l2) <= 3
    assert len(l3) <= 2


async def test_like_fallback_for_short_query(backend: SqliteMemoryBackend) -> None:
    sk = "ws:like"
    await backend.store(sk, _entry(id="e1", content="Go 语言入门"))
    results = await backend.search(sk, "Go", layers=["L2"])
    assert len(results) == 1


async def test_memory_ctx_nonzero_simulation(backend: SqliteMemoryBackend) -> None:
    sk = "ws:ctx"
    await backend.store(sk, _entry(id="f1", content="项目部署在 K8s 集群"))
    await backend.store(sk, _entry(id="f2", content="数据库使用 PostgreSQL"))
    await backend.store(sk, _entry(id="p1", layer="L3", content="部署流程: git tag → push → CI"))

    results = await backend.search(
        sk, "我该怎么把项目部署到生产环境",
        layers=["L2", "L3"],
        per_layer_limits={"L2": 3, "L3": 2},
    )
    assert len(results) > 0, "memory_ctx would be > 0 in real assemble()"


async def test_score_populated_for_fts5_hits(backend: SqliteMemoryBackend) -> None:
    sk = "ws:score"
    await backend.store(sk, _entry(id="e1", content="Redis 集群配置方法"))
    results = await backend.search(sk, "Redis 配置", layers=["L2"])
    assert len(results) >= 1
    assert results[0].score is not None
    assert results[0].score < 0
