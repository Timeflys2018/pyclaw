"""Integration tests for Curator + Memory lifecycle (Tasks 7.1–7.5).

These tests verify the full SOP lifecycle: store → archive → search exclusion,
including FTS5 integrity, dedup bypass for archived entries, and NULL last_used_at handling.

Task 7.6 is a manual step: run `pytest tests/ --ignore=tests/e2e` for full regression.
"""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock

import apsw
import pytest

from pyclaw.core.curator import _scan_single_db
from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend


def _create_procedure_db(db_path: Path, entries: list[dict]) -> None:
    """Create a test DB with procedures and FTS5 index."""
    conn = apsw.Connection(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    register_jieba_tokenizer(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS procedures (
        id TEXT PRIMARY KEY, session_key TEXT NOT NULL, type TEXT NOT NULL,
        content TEXT NOT NULL, source_session_id TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        last_used_at REAL, use_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active', archived_at REAL, archive_reason TEXT
    )""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS procedures_fts USING fts5(
        content, content=procedures, content_rowid=rowid, tokenize='jieba'
    )""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS procedures_ai AFTER INSERT ON procedures BEGIN
        INSERT INTO procedures_fts(rowid, content) VALUES (new.rowid, new.content);
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS procedures_au AFTER UPDATE OF content ON procedures BEGIN
        INSERT INTO procedures_fts(procedures_fts, rowid, content) VALUES('delete', old.rowid, old.content);
        INSERT INTO procedures_fts(rowid, content) VALUES (new.rowid, new.content);
    END""")
    for e in entries:
        conn.execute(
            "INSERT INTO procedures (id, session_key, type, content, source_session_id, "
            "created_at, updated_at, last_used_at, use_count, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (e["id"], e["session_key"], e.get("type", "auto_sop"), e["content"],
             "ses_test", e["created_at"], e["updated_at"],
             e.get("last_used_at"), e.get("use_count", 0), e.get("status", "active")),
        )
    conn.close()


# ─── Task 7.1: SOP → Curator archive → search doesn't return → L1 clean ────


@pytest.mark.asyncio
async def test_full_lifecycle_archive_removes_from_search(tmp_path: Path) -> None:
    """Store SOP → archive via curator scan → verify search returns empty."""
    db_path = tmp_path / "sk_test.db"
    old_time = time.time() - 100 * 86400  # 100 days ago

    _create_procedure_db(
        db_path,
        [
            {
                "id": "proc_deploy",
                "session_key": "sk_test",
                "content": "Deploy application via helm chart to kubernetes cluster",
                "created_at": old_time,
                "updated_at": old_time,
                "last_used_at": old_time,
                "use_count": 2,
            },
        ],
    )

    # Mock L1 index
    l1_index = AsyncMock()
    l1_index.index_remove = AsyncMock()

    archived_count, _graduated = await _scan_single_db(db_path, archive_days=90, l1_index=l1_index)
    assert archived_count == 1

    # Verify L1 index_remove was called
    l1_index.index_remove.assert_awaited_once_with("sk_test", "proc_deploy")

    # Verify search via SqliteMemoryBackend returns empty
    backend = SqliteMemoryBackend(base_dir=tmp_path)
    results = await backend.search("sk_test", "deploy helm kubernetes", layers=["L3"])
    assert results == []


# ─── Task 7.2: ForgetTool → archive → search empty → CLI visible ────────────


@pytest.mark.asyncio
async def test_forget_archives_and_search_excludes(tmp_path: Path) -> None:
    """Store SOP → archive_entry → verify search excludes → raw SQL shows archived."""
    backend = SqliteMemoryBackend(base_dir=tmp_path)

    # Store an active procedure via backend
    entry = MemoryEntry(
        id="proc_forget",
        layer="L3",
        type="auto_sop",
        content="Run database migration with alembic upgrade head",
        source_session_id="ses_test",
        created_at=time.time(),
        updated_at=time.time(),
        last_used_at=time.time(),
        use_count=1,
        status="active",
    )
    await backend.store("sk_forget", entry)

    # Verify search finds it initially
    results = await backend.search("sk_forget", "database migration alembic", layers=["L3"])
    assert len(results) == 1
    assert results[0].id == "proc_forget"

    # Archive it directly via SQL (simulating forget/archive logic)
    conn = await backend._get_conn("sk_forget")
    conn.execute(
        "UPDATE procedures SET status='archived', archived_at=?, archive_reason=? WHERE id=?",
        (time.time(), "user_forget", "proc_forget"),
    )

    # Search should now exclude the archived entry
    results = await backend.search("sk_forget", "database migration alembic", layers=["L3"])
    assert results == []

    # Raw SQL should show the entry with status='archived'
    row = conn.execute(
        "SELECT status, archive_reason FROM procedures WHERE id='proc_forget'"
    ).fetchone()
    assert row[0] == "archived"
    assert row[1] == "user_forget"


# ─── Task 7.3: Archive → re-extract not blocked by dedup ────────────────────


@pytest.mark.asyncio
async def test_archived_entry_not_blocking_dedup(tmp_path: Path) -> None:
    """After archiving, a similar new SOP should pass dedup check (search returns empty)."""
    backend = SqliteMemoryBackend(base_dir=tmp_path)

    # Store procedure with content "Deploy via helm"
    entry = MemoryEntry(
        id="proc_helm",
        layer="L3",
        type="auto_sop",
        content="Deploy application via helm chart to production cluster",
        source_session_id="ses_test",
        created_at=time.time(),
        updated_at=time.time(),
        last_used_at=time.time(),
        use_count=1,
        status="active",
    )
    await backend.store("sk_dedup", entry)

    # Verify it's findable
    results = await backend.search("sk_dedup", "Deploy application via helm chart", layers=["L3"])
    assert len(results) == 1

    # Archive it
    conn = await backend._get_conn("sk_dedup")
    conn.execute(
        "UPDATE procedures SET status='archived', archived_at=?, archive_reason=? WHERE id=?",
        (time.time(), "curator:90d_unused", "proc_helm"),
    )

    # Now searching for similar content returns empty (simulating _is_duplicate's search)
    results = await backend.search(
        "sk_dedup", "Deploy application via helm chart", layers=["L3"], limit=1
    )
    assert results == []
    # This proves _is_duplicate would return False for new similar content


# ─── Task 7.4: FTS5 trigger not fired on status change ──────────────────────


@pytest.mark.asyncio
async def test_fts5_not_triggered_by_archive(tmp_path: Path) -> None:
    """Status change should not trigger FTS reindex; content search still works after restore."""
    backend = SqliteMemoryBackend(base_dir=tmp_path)

    # Store procedure
    entry = MemoryEntry(
        id="proc_fts",
        layer="L3",
        type="auto_sop",
        content="Configure nginx reverse proxy with SSL termination",
        source_session_id="ses_test",
        created_at=time.time(),
        updated_at=time.time(),
        last_used_at=time.time(),
        use_count=1,
        status="active",
    )
    await backend.store("sk_fts", entry)

    conn = await backend._get_conn("sk_fts")

    # Archive it (status change only, not content change)
    conn.execute(
        "UPDATE procedures SET status='archived', archived_at=? WHERE id=?",
        (time.time(), "proc_fts"),
    )

    # Restore it (status back to active)
    conn.execute(
        "UPDATE procedures SET status='active', archived_at=NULL WHERE id=?",
        ("proc_fts",),
    )

    # Search by content → should still work (FTS intact, not corrupted by status changes)
    results = await backend.search("sk_fts", "nginx reverse proxy SSL", layers=["L3"])
    assert len(results) == 1
    assert results[0].id == "proc_fts"
    assert results[0].content == "Configure nginx reverse proxy with SSL termination"


# ─── Task 7.5: NULL last_used_at uses created_at as baseline ─────────────────


@pytest.mark.asyncio
async def test_null_last_used_at_uses_created_at(tmp_path: Path) -> None:
    """Procedure with NULL last_used_at should use created_at for age calculation."""
    db_path = tmp_path / "sk_null.db"

    now = time.time()
    fifty_days_ago = now - 50 * 86400
    hundred_days_ago = now - 100 * 86400

    _create_procedure_db(
        db_path,
        [
            {
                "id": "proc_young",
                "session_key": "sk_null",
                "content": "Young procedure only 50 days old",
                "created_at": fifty_days_ago,
                "updated_at": fifty_days_ago,
                "last_used_at": None,  # NULL — uses created_at
                "use_count": 0,
            },
            {
                "id": "proc_old",
                "session_key": "sk_null",
                "content": "Old procedure created 100 days ago",
                "created_at": hundred_days_ago,
                "updated_at": hundred_days_ago,
                "last_used_at": None,  # NULL — uses created_at
                "use_count": 0,
            },
        ],
    )

    l1_index = AsyncMock()
    l1_index.index_remove = AsyncMock()

    archived_count, _graduated = await _scan_single_db(db_path, archive_days=90, l1_index=l1_index)

    assert archived_count == 1

    # Verify proc_young is still active
    conn = apsw.Connection(str(db_path))
    young_row = conn.execute(
        "SELECT status FROM procedures WHERE id='proc_young'"
    ).fetchone()
    assert young_row[0] == "active"

    # Verify proc_old is archived
    old_row = conn.execute(
        "SELECT status, archive_reason FROM procedures WHERE id='proc_old'"
    ).fetchone()
    assert old_row[0] == "archived"
    assert "curator:90d_unused" in old_row[1]
    conn.close()

    # L1 index_remove called only for the archived entry
    l1_index.index_remove.assert_awaited_once_with("sk_null", "proc_old")
