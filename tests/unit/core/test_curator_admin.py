"""Tests for curator_admin pure ops (Phase A3-curator)."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import apsw
import pytest

from pyclaw.core.curator_admin import (
    last_review_timestamp,
    list_archived_sops,
    list_auto_sops,
    list_stale_sops,
    preview_graduation,
    restore_sop,
)
from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer

_SCHEMA_STATEMENTS = [
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
    )""",
]


def _seed_db(db_path: Path, session_key: str, rows: list[dict]) -> None:
    conn = apsw.Connection(str(db_path))
    try:
        register_jieba_tokenizer(conn)
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
        for r in rows:
            conn.execute(
                "INSERT INTO procedures (id, session_key, type, content, source_session_id, "
                "created_at, updated_at, last_used_at, use_count, status, archived_at, archive_reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    r["id"],
                    session_key,
                    r.get("type", "auto_sop"),
                    r.get("content", "x"),
                    r.get("source_session_id", "sess"),
                    r.get("created_at", time.time()),
                    r.get("updated_at", time.time()),
                    r.get("last_used_at"),
                    r.get("use_count", 0),
                    r.get("status", "active"),
                    r.get("archived_at"),
                    r.get("archive_reason"),
                ),
            )
    finally:
        conn.close()


@pytest.fixture
def settings(tmp_path: Path):
    s = MagicMock()
    s.memory.base_dir = str(tmp_path)
    s.evolution.curator.stale_after_days = 30
    s.evolution.curator.promotion_min_use_count = 5
    s.evolution.curator.promotion_min_days = 7
    return s


def test_list_auto_sops_scoped_to_session_key(settings, tmp_path: Path) -> None:
    _seed_db(
        tmp_path / "test_user_a.db",
        "test:user_a",
        [
            {"id": "p1", "type": "auto_sop", "status": "active"},
            {"id": "p2", "type": "manual", "status": "active"},
        ],
    )
    _seed_db(
        tmp_path / "test_user_b.db",
        "test:user_b",
        [
            {"id": "p3", "type": "auto_sop", "status": "active"},
        ],
    )

    scoped = list_auto_sops(settings, session_key="test:user_a")
    assert [r.entry_id for r in scoped] == ["p1"]


def test_list_auto_sops_global_view(settings, tmp_path: Path) -> None:
    _seed_db(
        tmp_path / "test_user_a.db",
        "test:user_a",
        [
            {"id": "p1", "type": "auto_sop"},
        ],
    )
    _seed_db(
        tmp_path / "test_user_b.db",
        "test:user_b",
        [
            {"id": "p2", "type": "auto_sop"},
        ],
    )

    all_sops = list_auto_sops(settings, session_key=None)
    assert sorted(r.entry_id for r in all_sops) == ["p1", "p2"]


def test_list_stale_sops_uses_threshold(settings, tmp_path: Path) -> None:
    old = time.time() - 100 * 86400
    fresh = time.time() - 5 * 86400
    _seed_db(
        tmp_path / "test_user_a.db",
        "test:user_a",
        [
            {"id": "old", "status": "active", "last_used_at": old, "created_at": old},
            {"id": "fresh", "status": "active", "last_used_at": fresh, "created_at": fresh},
        ],
    )

    stale = list_stale_sops(settings, session_key="test:user_a")
    assert [r.entry_id for r in stale] == ["old"]


def test_list_archived_sops(settings, tmp_path: Path) -> None:
    _seed_db(
        tmp_path / "test_user_a.db",
        "test:user_a",
        [
            {
                "id": "a1",
                "status": "archived",
                "archived_at": time.time(),
                "archive_reason": "curator:90d",
            },
            {"id": "a2", "status": "active"},
        ],
    )

    archived = list_archived_sops(settings, session_key="test:user_a")
    assert [r.entry_id for r in archived] == ["a1"]
    assert archived[0].archive_reason == "curator:90d"


def test_preview_graduation_respects_thresholds(settings, tmp_path: Path) -> None:
    old = time.time() - 30 * 86400
    recent = time.time() - 1 * 86400
    _seed_db(
        tmp_path / "test_user_a.db",
        "test:user_a",
        [
            {"id": "eligible", "type": "auto_sop", "use_count": 10, "created_at": old},
            {"id": "too_new", "type": "auto_sop", "use_count": 10, "created_at": recent},
            {"id": "low_use", "type": "auto_sop", "use_count": 2, "created_at": old},
        ],
    )

    preview = preview_graduation(settings, session_key="test:user_a")
    assert [r.entry_id for r in preview] == ["eligible"]


def test_restore_sop_flips_status(settings, tmp_path: Path) -> None:
    _seed_db(
        tmp_path / "test_user_a.db",
        "test:user_a",
        [
            {
                "id": "restoreme",
                "status": "archived",
                "archived_at": time.time(),
                "archive_reason": "curator",
            },
        ],
    )

    result = restore_sop("restoreme", settings, session_key="test:user_a")
    assert result.count == 1
    assert result.dbs_affected == 1

    still_archived = list_archived_sops(settings, session_key="test:user_a")
    assert still_archived == []

    active = list_auto_sops(settings, session_key="test:user_a")
    assert [r.entry_id for r in active] == ["restoreme"]


@pytest.mark.asyncio
async def test_last_review_timestamp_reads_key() -> None:
    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value="1700000000")
    ts = await last_review_timestamp(redis_client)
    assert ts == 1700000000


@pytest.mark.asyncio
async def test_last_review_timestamp_none_when_missing() -> None:
    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value=None)
    assert await last_review_timestamp(redis_client) is None


@pytest.mark.asyncio
async def test_last_review_timestamp_handles_none_client() -> None:
    assert await last_review_timestamp(None) is None
