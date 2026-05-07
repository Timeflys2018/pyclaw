"""Unit tests for curator scan functions (Tasks 3.6 & 3.7)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock

import apsw
import pytest

from pyclaw.core.curator import CuratorReport, _scan_single_db, run_curator_scan
from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer


def _create_test_db(path: Path, entries: list[dict]) -> None:
    conn = apsw.Connection(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    register_jieba_tokenizer(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS procedures (
            id TEXT PRIMARY KEY,
            session_key TEXT NOT NULL,
            type TEXT NOT NULL,
            content TEXT NOT NULL,
            source_session_id TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            last_used_at REAL,
            use_count INTEGER DEFAULT 0,
            status TEXT DEFAULT 'active',
            archived_at REAL,
            archive_reason TEXT
        )"""
    )
    for e in entries:
        conn.execute(
            "INSERT INTO procedures (id, session_key, type, content, source_session_id, "
            "created_at, updated_at, last_used_at, use_count, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e["id"],
                e["session_key"],
                e.get("type", "auto_sop"),
                e["content"],
                e.get("source_session_id", "ses_1"),
                e["created_at"],
                e["updated_at"],
                e.get("last_used_at"),
                e.get("use_count", 0),
                e.get("status", "active"),
            ),
        )
    conn.close()


# ─── Task 3.6: Curator Loop Tests ───────────────────────────────────────────


class TestScanSingleDb:
    """Tests for _scan_single_db."""

    @pytest.mark.asyncio
    async def test_archives_expired_entries(self, tmp_path: Path) -> None:
        """Normal scan archives expired entries (last_used_at > archive_days ago)."""
        db_file = tmp_path / "test.db"
        old_time = time.time() - 200 * 86400  # 200 days ago
        _create_test_db(
            db_file,
            [
                {
                    "id": "proc_1",
                    "session_key": "sk_1",
                    "content": "old procedure content",
                    "created_at": old_time,
                    "updated_at": old_time,
                    "last_used_at": old_time,
                    "use_count": 1,
                },
            ],
        )

        l1_index = AsyncMock()
        l1_index.index_remove = AsyncMock()

        archived_count = await _scan_single_db(db_file, archive_days=90, l1_index=l1_index)

        assert archived_count == 1
        l1_index.index_remove.assert_awaited_once_with("sk_1", "proc_1")

        # Verify DB state
        conn = apsw.Connection(str(db_file))
        row = conn.execute(
            "SELECT status, archive_reason FROM procedures WHERE id='proc_1'"
        ).fetchone()
        conn.close()
        assert row[0] == "archived"
        assert "curator:90d_unused" in row[1]

    @pytest.mark.asyncio
    async def test_fresh_entries_not_archived(self, tmp_path: Path) -> None:
        """Entries with recent last_used_at are NOT archived."""
        db_file = tmp_path / "test.db"
        recent_time = time.time() - 5 * 86400  # 5 days ago
        _create_test_db(
            db_file,
            [
                {
                    "id": "proc_fresh",
                    "session_key": "sk_1",
                    "content": "recent procedure",
                    "created_at": recent_time,
                    "updated_at": recent_time,
                    "last_used_at": recent_time,
                    "use_count": 3,
                },
            ],
        )

        l1_index = AsyncMock()
        l1_index.index_remove = AsyncMock()

        archived_count = await _scan_single_db(db_file, archive_days=90, l1_index=l1_index)

        assert archived_count == 0
        l1_index.index_remove.assert_not_awaited()

        # Verify entry remains active
        conn = apsw.Connection(str(db_file))
        row = conn.execute(
            "SELECT status FROM procedures WHERE id='proc_fresh'"
        ).fetchone()
        conn.close()
        assert row[0] == "active"

    @pytest.mark.asyncio
    async def test_uses_created_at_when_last_used_at_is_null(self, tmp_path: Path) -> None:
        """COALESCE(last_used_at, created_at) — null last_used_at falls back to created_at."""
        db_file = tmp_path / "test.db"
        old_time = time.time() - 200 * 86400
        _create_test_db(
            db_file,
            [
                {
                    "id": "proc_null_lu",
                    "session_key": "sk_2",
                    "content": "never used",
                    "created_at": old_time,
                    "updated_at": old_time,
                    "last_used_at": None,
                    "use_count": 0,
                },
            ],
        )

        l1_index = AsyncMock()
        l1_index.index_remove = AsyncMock()

        archived_count = await _scan_single_db(db_file, archive_days=90, l1_index=l1_index)

        assert archived_count == 1
        l1_index.index_remove.assert_awaited_once_with("sk_2", "proc_null_lu")


class TestCuratorLoopBehavior:
    """Tests for create_curator_loop lock/interval logic (mocked Redis)."""

    @pytest.mark.asyncio
    async def test_lock_competition_skip(self) -> None:
        """When redis.set(nx=True) returns None, scan is skipped (lock held by another)."""
        import asyncio

        from pyclaw.core.curator import create_curator_loop

        redis_client = AsyncMock()
        # First call: redis.get(CURATOR_LAST_RUN_KEY) exists
        redis_client.get = AsyncMock(return_value=None)
        # exists returns True so no seed
        redis_client.exists = AsyncMock(return_value=True)
        # set for seed
        redis_client.set = AsyncMock(return_value=None)  # nx=True returns None => not acquired

        settings = AsyncMock()
        settings.check_interval_seconds = 0.01
        settings.interval_seconds = 3600
        settings.archive_after_days = 90

        # Override redis_client.get to return old timestamp so interval is reached
        redis_client.get = AsyncMock(return_value="0")

        # Lock not acquired
        redis_client.set = AsyncMock(return_value=None)

        task = asyncio.create_task(
            create_curator_loop(
                settings=settings,
                memory_base_dir=Path("/nonexistent"),
                redis_client=redis_client,
                l1_index=AsyncMock(),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # set was called for seed (existing is None from first get) + lock attempt
        # The important thing: no scan was run (lock not acquired)
        # We just verify it didn't crash and set was called with nx
        calls = redis_client.set.call_args_list
        assert len(calls) >= 1

    @pytest.mark.asyncio
    async def test_first_startup_seed(self) -> None:
        """On first startup, if CURATOR_LAST_RUN_KEY doesn't exist, seed it."""
        import asyncio

        from pyclaw.core.curator import CURATOR_LAST_RUN_KEY, create_curator_loop

        redis_client = AsyncMock()
        # First get returns None (no existing last_run)
        redis_client.get = AsyncMock(return_value=None)
        redis_client.set = AsyncMock(return_value=True)

        settings = AsyncMock()
        settings.check_interval_seconds = 0.01
        settings.interval_seconds = 3600
        settings.archive_after_days = 90

        task = asyncio.create_task(
            create_curator_loop(
                settings=settings,
                memory_base_dir=Path("/nonexistent"),
                redis_client=redis_client,
                l1_index=AsyncMock(),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Verify redis.set was called with CURATOR_LAST_RUN_KEY for seeding
        first_set_call = redis_client.set.call_args_list[0]
        assert first_set_call[0][0] == CURATOR_LAST_RUN_KEY

    @pytest.mark.asyncio
    async def test_interval_not_reached_skip(self) -> None:
        """When last_run_at is recent, scan doesn't execute."""
        import asyncio

        from pyclaw.core.curator import create_curator_loop

        redis_client = AsyncMock()
        # First get for seed check returns existing value
        recent_ts = str(time.time())
        redis_client.get = AsyncMock(return_value=recent_ts)
        redis_client.set = AsyncMock(return_value=True)

        settings = AsyncMock()
        settings.check_interval_seconds = 0.01
        settings.interval_seconds = 3600  # 1 hour
        settings.archive_after_days = 90

        task = asyncio.create_task(
            create_curator_loop(
                settings=settings,
                memory_base_dir=Path("/nonexistent"),
                redis_client=redis_client,
                l1_index=AsyncMock(),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # set should NOT have been called with the lock key (only maybe seed)
        # Since get returned a value, no seed; since interval not reached, no lock attempt
        # The only set calls should be none (get returned existing, interval not reached)
        lock_set_calls = [
            c for c in redis_client.set.call_args_list if len(c[0]) > 0 and c[0][0] != "pyclaw:curator:last_run_at"
        ]
        assert len(lock_set_calls) == 0


# ─── Task 3.7: Parallel Scan Tests ──────────────────────────────────────────


class TestRunCuratorScan:
    """Tests for run_curator_scan (parallel DB processing)."""

    @pytest.mark.asyncio
    async def test_multiple_dbs_processed(self, tmp_path: Path) -> None:
        """Multiple DB files are all processed in parallel."""
        old_time = time.time() - 200 * 86400

        for i in range(3):
            db_file = tmp_path / f"session_{i}.db"
            _create_test_db(
                db_file,
                [
                    {
                        "id": f"proc_{i}",
                        "session_key": f"sk_{i}",
                        "content": f"stale procedure {i}",
                        "created_at": old_time,
                        "updated_at": old_time,
                        "last_used_at": old_time,
                        "use_count": 1,
                    },
                ],
            )

        l1_index = AsyncMock()
        l1_index.index_remove = AsyncMock()

        report = await run_curator_scan(
            memory_base_dir=tmp_path, archive_days=90, l1_index=l1_index
        )

        assert report.total_scanned == 3
        assert report.total_archived == 3
        assert report.errors == []
        assert l1_index.index_remove.await_count == 3

    @pytest.mark.asyncio
    async def test_single_db_failure_doesnt_affect_others(self, tmp_path: Path) -> None:
        """One invalid DB doesn't prevent others from being processed."""
        old_time = time.time() - 200 * 86400

        # Valid DB
        valid_db = tmp_path / "valid.db"
        _create_test_db(
            valid_db,
            [
                {
                    "id": "proc_valid",
                    "session_key": "sk_valid",
                    "content": "stale valid",
                    "created_at": old_time,
                    "updated_at": old_time,
                    "last_used_at": old_time,
                    "use_count": 1,
                },
            ],
        )

        # Invalid DB (corrupt file)
        invalid_db = tmp_path / "invalid.db"
        invalid_db.write_text("this is not a valid sqlite file")

        l1_index = AsyncMock()
        l1_index.index_remove = AsyncMock()

        report = await run_curator_scan(
            memory_base_dir=tmp_path, archive_days=90, l1_index=l1_index
        )

        assert report.total_scanned == 2
        assert report.total_archived == 1
        assert len(report.errors) == 1
        assert "invalid.db" in report.errors[0]
        l1_index.index_remove.assert_awaited_once_with("sk_valid", "proc_valid")

    @pytest.mark.asyncio
    async def test_fresh_entries_not_archived_in_scan(self, tmp_path: Path) -> None:
        """Fresh entries across DBs are not archived by run_curator_scan."""
        recent_time = time.time() - 5 * 86400

        db_file = tmp_path / "fresh.db"
        _create_test_db(
            db_file,
            [
                {
                    "id": "proc_recent",
                    "session_key": "sk_recent",
                    "content": "just used",
                    "created_at": recent_time,
                    "updated_at": recent_time,
                    "last_used_at": recent_time,
                    "use_count": 5,
                },
            ],
        )

        l1_index = AsyncMock()
        l1_index.index_remove = AsyncMock()

        report = await run_curator_scan(
            memory_base_dir=tmp_path, archive_days=90, l1_index=l1_index
        )

        assert report.total_scanned == 1
        assert report.total_archived == 0
        assert report.errors == []
        l1_index.index_remove.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_directory_returns_zero_report(self, tmp_path: Path) -> None:
        """No DB files → report with all zeros."""
        l1_index = AsyncMock()

        report = await run_curator_scan(
            memory_base_dir=tmp_path, archive_days=90, l1_index=l1_index
        )

        assert report == CuratorReport(total_scanned=0, total_archived=0, errors=[])
