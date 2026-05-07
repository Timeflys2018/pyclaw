"""Curator background loop — archives stale memory entries via SETNX distributed lock."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import apsw

from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer

logger = logging.getLogger(__name__)

CURATOR_LOCK_KEY = "pyclaw:curator:lock"
CURATOR_LAST_RUN_KEY = "pyclaw:curator:last_run_at"
SCAN_CONCURRENCY = 10


@dataclass
class CuratorReport:
    total_scanned: int = 0
    total_archived: int = 0
    errors: list[str] = field(default_factory=list)


async def create_curator_loop(
    settings: Any,
    memory_base_dir: Path,
    redis_client: Any,
    l1_index: Any,
) -> None:

    existing = await redis_client.get(CURATOR_LAST_RUN_KEY)
    if existing is None:
        await redis_client.set(CURATOR_LAST_RUN_KEY, str(time.time()))

    try:
        while True:
            await asyncio.sleep(settings.check_interval_seconds)

            raw_last_run = await redis_client.get(CURATOR_LAST_RUN_KEY)
            if raw_last_run is not None:
                try:
                    last_run_at = float(raw_last_run)
                except (ValueError, TypeError):
                    last_run_at = 0.0
                if time.time() - last_run_at < settings.interval_seconds:
                    continue

            acquired = await redis_client.set(
                CURATOR_LOCK_KEY,
                "1",
                ex=settings.interval_seconds,
                nx=True,
            )
            if not acquired:
                continue

            try:
                report = await run_curator_scan(
                    memory_base_dir=memory_base_dir,
                    archive_days=settings.archive_after_days,
                    l1_index=l1_index,
                )
                await redis_client.set(CURATOR_LAST_RUN_KEY, str(time.time()))
                logger.info(
                    "Curator scan complete: scanned=%d archived=%d errors=%d",
                    report.total_scanned,
                    report.total_archived,
                    len(report.errors),
                )
                if report.errors:
                    for err in report.errors[:5]:
                        logger.warning("Curator scan error: %s", err)
            finally:
                try:
                    await redis_client.delete(CURATOR_LOCK_KEY)
                except Exception:
                    logger.debug("Curator lock release failed", exc_info=True)
    except asyncio.CancelledError:
        return


async def run_curator_scan(
    memory_base_dir: Path,
    archive_days: int,
    l1_index: Any,
) -> CuratorReport:

    db_files = sorted(
        f
        for f in memory_base_dir.glob("*.db")
        if not f.name.endswith(("-wal", "-shm"))
    )

    report = CuratorReport(total_scanned=len(db_files))
    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def _bounded_scan(db_file: Path) -> int:
        async with semaphore:
            return await _scan_single_db(db_file, archive_days, l1_index)

    results = await asyncio.gather(
        *[_bounded_scan(f) for f in db_files],
        return_exceptions=True,
    )

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            report.errors.append(f"{db_files[i].name}: {result!r}")
        elif isinstance(result, int):
            report.total_archived += result

    return report


async def _scan_single_db(
    db_file: Path,
    archive_days: int,
    l1_index: Any,
) -> int:

    threshold = time.time() - archive_days * 86400

    def _do_scan() -> list[tuple[str, str]]:
        conn = apsw.Connection(str(db_file))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            register_jieba_tokenizer(conn)

            cursor = conn.execute(
                (
                    "SELECT id, session_key FROM procedures "
                    "WHERE status='active' "
                    "AND COALESCE(last_used_at, created_at) < ?"
                ),
                (threshold,),
            )
            stale_rows = cursor.fetchall()

            if not stale_rows:
                return []

            ids = [row[0] for row in stale_rows]
            now = time.time()
            reason = f"curator:{archive_days}d_unused"
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                (
                    f"UPDATE procedures SET status='archived', "  # noqa: S608
                    f"archived_at=?, archive_reason=? "
                    f"WHERE id IN ({placeholders})"
                ),
                [now, reason, *ids],
            )

            return [(str(row[0]), str(row[1])) for row in stale_rows]
        finally:
            conn.close()

    archived_rows = await asyncio.to_thread(_do_scan)

    for entry_id, session_key in archived_rows:
        try:
            await l1_index.index_remove(session_key, entry_id)
        except Exception:
            pass

    return len(archived_rows)
