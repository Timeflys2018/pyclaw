# pyright: reportArgumentType=false, reportGeneralTypeIssues=false, reportMissingTypeArgument=false
"""Phase 2 E2E: Curator full cycle with real SQLite + real Redis.

构造一个 seed 数据库 + 真实 Redis 跑一次完整的 CuratorCycle.execute()，
验证 curator refactor 后的核心路径端到端工作：

  1. **Seed**: 30 条 auto_sop (10 active + 10 stale + 10 archived)
  2. **Normal cycle**: scan 归档 10 条 stale → state_store 写入成功
  3. **Exception cycle**: 人为让 scan 抛异常 → unexpected_exception=True + 不崩溃

测试目录: /tmp/pyclaw-phase2-e2e/ (跑完清理)
Redis prefix: phase2-e2e: (跑完清理)
生产 ~/.pyclaw/memory/ 和 pyclaw:* 前缀完全不受影响。

使用:
  .venv/bin/python scripts/e2e_phase2_curator.py
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
import time
import traceback
from pathlib import Path
from unittest.mock import AsyncMock

import apsw
import redis.asyncio as aioredis

from pyclaw.core.curator_cycle import CuratorCycle
from pyclaw.core.curator_state import CuratorStateStore
from pyclaw.infra.task_manager import TaskManager
from pyclaw.storage.lock.redis import RedisLockManager

PHASE2_REDIS_PREFIX = "phase2-e2e:"
PHASE2_MEMORY_DIR = Path("/tmp/pyclaw-phase2-e2e")

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


class _FakeCuratorSettings:
    """Minimal curator settings for cycle execution."""

    archive_after_days = 30
    promotion_min_days = 7
    promotion_min_use_count = 5
    check_interval_seconds = 60
    interval_seconds = 0
    llm_review_enabled = False
    graduation_enabled = False
    graduation_mode = "template"


def load_redis_config() -> dict:
    path = Path(__file__).parent.parent / "configs" / "pyclaw.json"
    with open(path) as f:
        return json.load(f)["redis"]


async def make_redis_client() -> aioredis.Redis:
    cfg = load_redis_config()
    return aioredis.Redis(
        host=cfg["host"],
        port=cfg["port"],
        password=cfg.get("password"),
        decode_responses=False,
    )


async def cleanup_phase2(client: aioredis.Redis) -> None:
    """Clean up Redis keys and local test directory."""
    async for key in client.scan_iter(match=f"{PHASE2_REDIS_PREFIX}*", count=100):
        await client.delete(key)

    if PHASE2_MEMORY_DIR.exists():
        shutil.rmtree(PHASE2_MEMORY_DIR)


def seed_database(db_path: Path, active: int = 10, stale: int = 10, archived: int = 10) -> None:
    """Seed sqlite db with 3 cohorts: active recent / stale old / already archived."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = apsw.Connection(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS procedures (
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
        )
    """)

    now = time.time()
    entries: list = []

    for i in range(active):
        entries.append((
            f"sop_active_{i:03d}", "test:e2e", "auto_sop",
            f"Active SOP #{i}\nRecent usage\nStep 1\nStep 2",
            "ses_test", now - 5 * 86400, now - 5 * 86400,
            now - i * 3600, 5 + i, "active", None, None,
        ))

    for i in range(stale):
        entries.append((
            f"sop_stale_{i:03d}", "test:e2e", "auto_sop",
            f"Stale SOP #{i}\nUnused for 40 days\nStep 1\nStep 2",
            "ses_test", now - 45 * 86400, now - 45 * 86400,
            now - 40 * 86400, 1, "active", None, None,
        ))

    for i in range(archived):
        entries.append((
            f"sop_archived_{i:03d}", "test:e2e", "auto_sop",
            f"Already archived SOP #{i}",
            "ses_test", now - 100 * 86400, now - 100 * 86400,
            now - 100 * 86400, 0, "archived",
            now - 95 * 86400, "pre-archived for test",
        ))

    conn.executemany(
        "INSERT INTO procedures (id, session_key, type, content, source_session_id, "
        "created_at, updated_at, last_used_at, use_count, status, archived_at, archive_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        entries,
    )
    conn.close()


def get_status_counts(db_path: Path) -> dict[str, int]:
    """Return procedure counts grouped by status."""
    conn = apsw.Connection(str(db_path))
    try:
        rows = list(conn.execute("SELECT status, COUNT(*) FROM procedures GROUP BY status"))
    finally:
        conn.close()
    return {row[0]: row[1] for row in rows}


async def run_phase2() -> int:
    print(f"{CYAN}=== Phase 2 E2E: Curator full cycle with real data ==={RESET}")
    cfg = load_redis_config()
    print(f"Redis:     {cfg['host']}:{cfg['port']} (prefix: {PHASE2_REDIS_PREFIX})")
    print(f"Memory:    {PHASE2_MEMORY_DIR} (isolated from prod ~/.pyclaw/memory)")

    client = await make_redis_client()
    failures: list[str] = []

    try:
        await cleanup_phase2(client)

        db_path = PHASE2_MEMORY_DIR / "test_e2e.db"
        seed_database(db_path)

        initial = get_status_counts(db_path)
        print(f"\n{CYAN}[Setup] seeded {sum(initial.values())} SOPs: {initial}{RESET}")

        assert initial == {"active": 20, "archived": 10}, \
            f"seed wrong: {initial}"

        lock_manager = RedisLockManager(client, key_prefix=PHASE2_REDIS_PREFIX)
        tm = TaskManager()
        state_store = CuratorStateStore(client)
        settings = _FakeCuratorSettings()

        # =========================================================
        # Scenario 1: Normal cycle — scan archives 10 stale SOPs
        # =========================================================
        print(f"\n{CYAN}[Scenario 1] Normal cycle (should archive 10 stale){RESET}")

        cycle = CuratorCycle(
            memory_base_dir=PHASE2_MEMORY_DIR,
            settings=settings,
            state_store=state_store,
            lock_manager=lock_manager,
            task_manager=tm,
            l1_index=AsyncMock(),
            mode="scan_and_review",
            force_review=False,
            owner_label="phase2-normal",
        )

        t0 = time.time()
        report = await cycle.execute()
        elapsed = time.time() - t0

        print(f"  elapsed:              {elapsed:.2f}s")
        print(f"  report.acquired:      {report.acquired}")
        print(f"  report.error:         {report.error!r}")
        print(f"  report.unexpected:    {report.unexpected_exception}")
        print(f"  report.scan_report:   {report.scan_report}")

        if not report.acquired:
            failures.append("Scenario 1: report.acquired should be True")
        if report.error not in (None, "review_skipped_interval"):
            failures.append(
                f"Scenario 1: report.error should be None or review_skipped_interval, "
                f"got {report.error!r}"
            )
        if report.unexpected_exception:
            failures.append("Scenario 1: report.unexpected_exception should be False")
        if report.scan_report is None:
            failures.append("Scenario 1: scan_report should be populated")
        else:
            if report.scan_report.total_archived != 10:
                failures.append(
                    f"Scenario 1: expected 10 archived, got "
                    f"{report.scan_report.total_archived}"
                )
            if report.scan_report.total_scanned != 1:
                failures.append(
                    f"Scenario 1: expected 1 db scanned, got "
                    f"{report.scan_report.total_scanned}"
                )

        post_scan = get_status_counts(db_path)
        print(f"  post-cycle counts:    {post_scan}")

        expected_post = {"active": 10, "archived": 20}
        if post_scan != expected_post:
            failures.append(f"Scenario 1: db state wrong. expected {expected_post}, got {post_scan}")

        last_run = await state_store.get_last_scan_at()
        if last_run is None:
            failures.append("Scenario 1: state_store.get_last_scan_at() returned None after cycle")
        elif abs(last_run - time.time()) > 10:
            failures.append(f"Scenario 1: last_run_at timestamp off: {last_run} vs {time.time()}")
        else:
            print(f"  state_store scan_ts:  {last_run:.2f} (✓ within 10s of now)")

        if failures:
            print(f"  {RED}Scenario 1: {len(failures)} failure(s){RESET}")
        else:
            print(f"  {GREEN}Scenario 1 PASS: real curator cycle archived 10 stale SOPs end-to-end{RESET}")

        # =========================================================
        # Scenario 2: Exception in scan → unexpected_exception=True
        # =========================================================
        print(f"\n{CYAN}[Scenario 2] Force scan to throw → unexpected_exception=True{RESET}")

        import pyclaw.core.curator as _curator
        original_scan = _curator.run_curator_scan

        async def _failing_scan(*args, **kwargs):
            raise RuntimeError("simulated scan failure")

        _curator.run_curator_scan = _failing_scan  # type: ignore[assignment]

        try:
            cycle2 = CuratorCycle(
                memory_base_dir=PHASE2_MEMORY_DIR,
                settings=settings,
                state_store=state_store,
                lock_manager=lock_manager,
                task_manager=tm,
                l1_index=AsyncMock(),
                mode="scan_and_review",
                force_review=False,
                owner_label="phase2-exception",
            )
            report2 = await cycle2.execute()

            print(f"  report.acquired:      {report2.acquired}")
            print(f"  report.error:         {report2.error!r}")
            print(f"  report.unexpected:    {report2.unexpected_exception}")
            print(f"  report.scan_report:   {report2.scan_report}")

            if not report2.acquired:
                failures.append("Scenario 2: acquired should be True (lock was acquired before scan)")
            if report2.error is not None:
                failures.append(f"Scenario 2: error should be None (not lock_lost), got {report2.error!r}")
            if not report2.unexpected_exception:
                failures.append("Scenario 2: unexpected_exception should be True")
            if report2.scan_report is not None:
                failures.append("Scenario 2: scan_report should be None (scan failed)")

            if len([f for f in failures if "Scenario 2" in f]) == 0:
                print(f"  {GREEN}Scenario 2 PASS: Phase C1b observability flag fires on scan exception{RESET}")
            else:
                print(f"  {RED}Scenario 2: failures{RESET}")

        finally:
            _curator.run_curator_scan = original_scan  # type: ignore[assignment]

        # =========================================================
        # Scenario 3: Second cycle must succeed (lock was released)
        # =========================================================
        print(f"\n{CYAN}[Scenario 3] Lock released after exception → next cycle acquires{RESET}")

        cycle3 = CuratorCycle(
            memory_base_dir=PHASE2_MEMORY_DIR,
            settings=settings,
            state_store=state_store,
            lock_manager=lock_manager,
            task_manager=tm,
            l1_index=AsyncMock(),
            mode="review_only",
            force_review=False,
            owner_label="phase2-third",
        )
        report3 = await cycle3.execute()

        if not report3.acquired:
            failures.append(f"Scenario 3: acquired=False — lock was not released after Scenario 2")
        else:
            print(f"  {GREEN}Scenario 3 PASS: lock released cleanly, next cycle acquired{RESET}")

    finally:
        print(f"\n{CYAN}Cleanup: removing {PHASE2_MEMORY_DIR} and phase2-e2e:* keys{RESET}")
        await cleanup_phase2(client)
        await client.aclose()

    print()
    print("=" * 60)
    if failures:
        print(f"{RED}Phase 2 Summary: {len(failures)} failure(s){RESET}")
        print("=" * 60)
        for f in failures:
            print(f"  {RED}✗ {f}{RESET}")
        return 1
    else:
        print(f"{GREEN}Phase 2 Summary: ALL 3 scenarios passed (7 assertions, 1 db roundtrip){RESET}")
        print("=" * 60)
        return 0


if __name__ == "__main__":
    try:
        exit_code = asyncio.run(run_phase2())
    except KeyboardInterrupt:
        print(f"\n{YELLOW}Interrupted{RESET}")
        sys.exit(130)
    except Exception as exc:
        print(f"\n{RED}FATAL: {exc}{RESET}")
        traceback.print_exc()
        sys.exit(2)
    sys.exit(exit_code)
