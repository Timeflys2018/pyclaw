from __future__ import annotations

import apsw
import pytest

from pyclaw.storage.memory.jieba_tokenizer import (
    build_safe_match_query,
    register_jieba_tokenizer,
)


@pytest.fixture
def conn() -> apsw.Connection:
    c = apsw.Connection(":memory:")
    register_jieba_tokenizer(c)
    c.execute("CREATE VIRTUAL TABLE t USING fts5(content, tokenize='jieba')")
    return c


def _insert(conn: apsw.Connection, text: str) -> None:
    conn.execute("INSERT INTO t VALUES (?)", (text,))


def _search(conn: apsw.Connection, query: str) -> list[str]:
    match_q = build_safe_match_query(query)
    if match_q is None:
        return []
    return [row[0] for row in conn.execute("SELECT content FROM t WHERE t MATCH ?", (match_q,))]


def test_chinese_text_segmented_correctly(conn: apsw.Connection) -> None:
    _insert(conn, "项目部署在K8s集群")
    results = _search(conn, "项目")
    assert len(results) == 1
    assert "项目部署在K8s集群" in results


def test_chinese_keywords_match(conn: apsw.Connection) -> None:
    _insert(conn, "项目部署在K8s集群")
    results = _search(conn, "部署")
    assert len(results) == 1

    results_k8s = _search(conn, "k8s")
    assert len(results_k8s) == 1

    results_cluster = _search(conn, "集群")
    assert len(results_cluster) == 1


def test_stop_words_filtered_from_index(conn: apsw.Connection) -> None:
    _insert(conn, "我该怎么把项目部署到生产环境")
    results_wo = _search(conn, "我该")
    assert len(results_wo) == 0

    results_zenme = _search(conn, "怎么")
    assert len(results_zenme) == 0

    results_ba = _search(conn, "把")
    assert len(results_ba) == 0


def test_programming_keywords_preserved(conn: apsw.Connection) -> None:
    _insert(conn, "for 循环的用法")
    results = _search(conn, "for")
    assert len(results) == 1

    results_loop = _search(conn, "循环")
    assert len(results_loop) == 1


def test_english_tech_terms_preserved(conn: apsw.Connection) -> None:
    _insert(conn, "Redis connection timeout error")
    results = _search(conn, "redis")
    assert len(results) == 1

    results_conn = _search(conn, "connection")
    assert len(results_conn) == 1

    results_timeout = _search(conn, "timeout")
    assert len(results_timeout) == 1


def test_natural_language_query_matches(conn: apsw.Connection) -> None:
    _insert(conn, "项目部署在 K8s 集群")
    _insert(conn, "我喜欢简洁的回答")
    _insert(conn, "部署流程: git tag → push")

    results = _search(conn, "我该怎么把项目部署到生产环境")
    assert len(results) >= 1
    assert any("部署" in r for r in results)


def test_build_safe_match_query_escapes_fts5_syntax() -> None:
    query = "C++ OR Python 哪个好"
    result = build_safe_match_query(query)
    assert result is not None
    assert "c++" in result.lower()
    assert "python" in result.lower()


def test_build_safe_match_query_pure_stop_words_returns_none() -> None:
    result = build_safe_match_query("我的是了")
    assert result is None


def test_build_safe_match_query_empty_returns_none() -> None:
    assert build_safe_match_query("") is None
    assert build_safe_match_query("   ") is None


def test_bm25_rank_ordering(conn: apsw.Connection) -> None:
    _insert(conn, "部署 部署 部署 很多次部署")
    _insert(conn, "只提到一次部署")

    match_q = build_safe_match_query("部署")
    rows = list(
        conn.execute("SELECT content, rank FROM t WHERE t MATCH ? ORDER BY rank", (match_q,))
    )
    assert len(rows) == 2
    assert rows[0][1] <= rows[1][1]
