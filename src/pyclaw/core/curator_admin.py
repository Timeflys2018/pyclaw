"""Curator admin pure ops shared by CLI and Chat handlers (Phase A3-curator)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyclaw.core.curator_state import CuratorStateStore
from pyclaw.storage.memory.naming import (
    DbFileNamingPolicy,
    HashOnlyNaming,
    HumanReadableNaming,
)


def _naming_policy_from_settings(settings: Any) -> DbFileNamingPolicy:
    policy = getattr(getattr(settings, "memory", None), "naming_policy", "human")
    return HashOnlyNaming() if policy == "hash" else HumanReadableNaming()


@dataclass
class SopRow:
    entry_id: str
    session_key: str
    content: str
    use_count: int
    last_used_at: float | None


@dataclass
class ArchivedSopRow:
    entry_id: str
    session_key: str
    content: str
    archived_at: float | None
    archive_reason: str | None


@dataclass
class GraduationResult:
    ok: bool
    skill_path: str | None
    error: str | None = None


@dataclass
class RestoreResult:
    count: int
    dbs_affected: int


def _memory_base_dir(settings: Any) -> Path:
    return Path(settings.memory.base_dir).expanduser()


def _get_memory_dbs(settings: Any, *, session_key: str | None = None) -> list[Path]:
    base = _memory_base_dir(settings)
    if not base.is_dir():
        return []
    if session_key is not None:
        naming = _naming_policy_from_settings(settings)
        db_name = naming.filename_for(session_key)
        candidate = base / db_name
        resolved = candidate.resolve()
        if not resolved.is_relative_to(base.resolve()):
            raise ValueError(
                f"session_key {session_key!r} produced out-of-base db path: {resolved}",
            )
        return [candidate] if candidate.is_file() else []
    return sorted(p for p in base.glob("*.db") if not p.name.endswith(("-wal", "-shm")))


def _open_db(path: Path):
    import apsw

    from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer

    conn = apsw.Connection(str(path))
    register_jieba_tokenizer(conn)
    return conn


def _list_sops(
    settings: Any,
    session_key: str | None,
    where_sql: str,
    params: tuple[Any, ...] = (),
) -> list[SopRow]:
    results: list[SopRow] = []
    for db_path in _get_memory_dbs(settings, session_key=session_key):
        conn = _open_db(db_path)
        try:
            sql = (
                "SELECT id, session_key, content, use_count, last_used_at "
                f"FROM procedures WHERE {where_sql}"
            )
            for row in conn.execute(sql, params):
                results.append(SopRow(
                    entry_id=str(row[0]),
                    session_key=str(row[1]),
                    content=str(row[2]),
                    use_count=int(row[3] or 0),
                    last_used_at=float(row[4]) if row[4] is not None else None,
                ))
        finally:
            conn.close()
    return results


def list_auto_sops(settings: Any, session_key: str | None = None) -> list[SopRow]:
    return _list_sops(
        settings,
        session_key,
        "type='auto_sop' AND status='active'",
    )


def list_stale_sops(settings: Any, session_key: str | None = None) -> list[SopRow]:
    stale_days = settings.evolution.curator.stale_after_days
    threshold = time.time() - stale_days * 86400
    return _list_sops(
        settings,
        session_key,
        "status='active' AND COALESCE(last_used_at, created_at) < ?",
        (threshold,),
    )


def list_archived_sops(
    settings: Any, session_key: str | None = None
) -> list[ArchivedSopRow]:
    results: list[ArchivedSopRow] = []
    for db_path in _get_memory_dbs(settings, session_key=session_key):
        conn = _open_db(db_path)
        try:
            for row in conn.execute(
                "SELECT id, session_key, content, archived_at, archive_reason "
                "FROM procedures WHERE status='archived'"
            ):
                results.append(ArchivedSopRow(
                    entry_id=str(row[0]),
                    session_key=str(row[1]),
                    content=str(row[2]),
                    archived_at=float(row[3]) if row[3] is not None else None,
                    archive_reason=str(row[4]) if row[4] is not None else None,
                ))
        finally:
            conn.close()
    return results


def preview_graduation(
    settings: Any, session_key: str | None = None
) -> list[SopRow]:
    min_use = getattr(settings.evolution.curator, "promotion_min_use_count", 5)
    min_days = getattr(settings.evolution.curator, "promotion_min_days", 7)
    threshold = time.time() - min_days * 86400
    return _list_sops(
        settings,
        session_key,
        "type='auto_sop' AND status='active' AND use_count >= ? AND created_at <= ?",
        (min_use, threshold),
    )


def restore_sop(
    entry_id: str, settings: Any, session_key: str | None = None
) -> RestoreResult:
    count = 0
    dbs_affected = 0
    for db_path in _get_memory_dbs(settings, session_key=session_key):
        conn = _open_db(db_path)
        try:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM procedures WHERE id=? AND status='archived'",
                (entry_id,),
            )
            row = cursor.fetchone()
            if row and int(row[0]) > 0:
                conn.execute(
                    "UPDATE procedures SET status='active', archived_at=NULL, archive_reason=NULL WHERE id=?",
                    (entry_id,),
                )
                count += 1
                dbs_affected += 1
        finally:
            conn.close()
    return RestoreResult(count=count, dbs_affected=dbs_affected)


async def last_review_timestamp(redis_client: Any) -> int | None:
    if redis_client is None:
        return None
    return await CuratorStateStore(redis_client).get_last_review_at()
