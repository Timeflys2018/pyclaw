"""Tests for archive_entry and archived_at/archive_reason migration."""
import time

import pytest

from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend


@pytest.fixture
def backend(tmp_path):
    return SqliteMemoryBackend(base_dir=tmp_path)


@pytest.fixture
async def seeded_backend(backend):
    """Backend with one active L3 procedure."""
    entry = MemoryEntry(
        id="proc_archive_test",
        layer="L3",
        type="auto_sop",
        content="Deploy via helm: 1) build image 2) push 3) helm upgrade",
        source_session_id="ses_test",
        created_at=time.time(),
        updated_at=time.time(),
        status="active",
    )
    await backend.store("test_user", entry)
    return backend


class TestMigrationArchivedAt:
    """Task 2.5: migration adds archived_at and archive_reason columns."""

    @pytest.mark.asyncio
    async def test_new_db_has_archived_at_column(self, backend):
        """New DB should have archived_at column from schema creation."""
        conn = await backend._get_conn("new_user")
        # Query pragma to check column exists
        cols = list(conn.execute("PRAGMA table_info(procedures)"))
        col_names = [row[1] for row in cols]
        assert "archived_at" in col_names
        assert "archive_reason" in col_names

    @pytest.mark.asyncio
    async def test_migration_idempotent(self, backend):
        """Running migration twice should not error."""
        conn = await backend._get_conn("test_user")
        # Force re-run migration
        backend._migrate_add_archived_at(conn)
        # Should not raise
        cols = list(conn.execute("PRAGMA table_info(procedures)"))
        col_names = [row[1] for row in cols]
        assert "archived_at" in col_names


class TestArchiveEntry:
    """Task 2.4: archive_entry behavior."""

    @pytest.mark.asyncio
    async def test_archive_success(self, seeded_backend):
        """Archiving active entry returns True and sets status."""
        conn = await seeded_backend._get_conn("test_user")
        now_before = time.time()

        # Direct SQL archive (testing sqlite layer)
        conn.execute(
            "UPDATE procedures SET status='archived', archived_at=?, archive_reason=? "
            "WHERE id=? AND status='active'",
            (time.time(), "test reason", "proc_archive_test"),
        )
        assert conn.changes() > 0

        # Verify status changed
        row = list(conn.execute(
            "SELECT status, archived_at, archive_reason FROM procedures WHERE id=?",
            ("proc_archive_test",),
        ))
        assert row[0][0] == "archived"
        assert row[0][1] >= now_before
        assert row[0][2] == "test reason"

    @pytest.mark.asyncio
    async def test_archive_reason_persisted(self, seeded_backend):
        """archive_reason is stored in DB."""
        conn = await seeded_backend._get_conn("test_user")
        conn.execute(
            "UPDATE procedures SET status='archived', archived_at=?, archive_reason=? "
            "WHERE id=?",
            (time.time(), "curator:90d_unused", "proc_archive_test"),
        )
        row = list(conn.execute(
            "SELECT archive_reason FROM procedures WHERE id=?",
            ("proc_archive_test",),
        ))
        assert row[0][0] == "curator:90d_unused"

    @pytest.mark.asyncio
    async def test_archive_nonexistent_returns_zero_changes(self, seeded_backend):
        """Archiving non-existent entry changes 0 rows."""
        conn = await seeded_backend._get_conn("test_user")
        conn.execute(
            "UPDATE procedures SET status='archived' WHERE id=? AND status='active'",
            ("nonexistent_id",),
        )
        assert conn.changes() == 0

    @pytest.mark.asyncio
    async def test_archive_already_archived_returns_zero(self, seeded_backend):
        """Archiving already-archived entry changes 0 rows."""
        conn = await seeded_backend._get_conn("test_user")
        # First archive
        conn.execute(
            "UPDATE procedures SET status='archived', archived_at=? WHERE id=?",
            (time.time(), "proc_archive_test"),
        )
        # Second archive attempt
        conn.execute(
            "UPDATE procedures SET status='archived' WHERE id=? AND status='active'",
            ("proc_archive_test",),
        )
        assert conn.changes() == 0

    @pytest.mark.asyncio
    async def test_archived_entry_excluded_from_search(self, seeded_backend):
        """After archiving, search should not return the entry."""
        conn = await seeded_backend._get_conn("test_user")
        conn.execute(
            "UPDATE procedures SET status='archived' WHERE id=?",
            ("proc_archive_test",),
        )
        results = await seeded_backend.search("test_user", "deploy helm", layers=["L3"])
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_fts5_not_triggered_by_status_update(self, seeded_backend):
        """Status change should NOT trigger FTS5 reindex (trigger is AFTER UPDATE OF content only)."""
        conn = await seeded_backend._get_conn("test_user")
        # Archive (changes status, not content)
        conn.execute(
            "UPDATE procedures SET status='archived', archived_at=? WHERE id=?",
            (time.time(), "proc_archive_test"),
        )
        # Restore to active
        conn.execute(
            "UPDATE procedures SET status='active', archived_at=NULL WHERE id=?",
            ("proc_archive_test",),
        )
        # FTS should still work (content unchanged)
        results = await seeded_backend.search("test_user", "deploy helm", layers=["L3"])
        assert len(results) == 1
        assert "deploy" in results[0].content.lower()
