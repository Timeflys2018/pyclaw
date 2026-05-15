"""Integration tests for curator cycle concurrency + heartbeat (Phase E).

Requires real Redis — set PYCLAW_TEST_REDIS_HOST env var to run.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.core.curator import (
    CURATOR_CYCLE_LOCK_KEY,
    CURATOR_LAST_RUN_KEY,
    CURATOR_LLM_REVIEW_KEY,
    CuratorReport,
    create_curator_loop,
    run_curator_cycle,
)
from pyclaw.infra.task_manager import TaskManager
from pyclaw.storage.lock.redis import RedisLockManager

pytestmark = pytest.mark.skipif(
    not os.environ.get("PYCLAW_TEST_REDIS_HOST"),
    reason="PYCLAW_TEST_REDIS_HOST not set — skipping curator concurrency integration",
)


TEST_KEY_PREFIX = "pyclaw_test_concurrency:"


@pytest.fixture
async def redis_client():
    import redis.asyncio as aioredis

    host = os.environ.get("PYCLAW_TEST_REDIS_HOST", "localhost")
    port = int(os.environ.get("PYCLAW_TEST_REDIS_PORT", "6379"))
    password = os.environ.get("PYCLAW_TEST_REDIS_PASSWORD") or None
    client = aioredis.Redis(host=host, port=port, password=password, decode_responses=True)
    try:
        await client.ping()
    except Exception:
        pytest.skip("Redis not reachable")

    await client.delete(
        f"{TEST_KEY_PREFIX}{CURATOR_CYCLE_LOCK_KEY}",
        CURATOR_LAST_RUN_KEY,
        CURATOR_LLM_REVIEW_KEY,
    )

    yield client

    await client.delete(
        f"{TEST_KEY_PREFIX}{CURATOR_CYCLE_LOCK_KEY}",
        CURATOR_LAST_RUN_KEY,
        CURATOR_LLM_REVIEW_KEY,
    )
    await client.aclose()


@pytest.fixture
def lock_manager(redis_client):
    return RedisLockManager(redis_client, key_prefix=TEST_KEY_PREFIX)


@pytest.fixture
async def task_manager():
    tm = TaskManager()
    yield tm
    await tm.shutdown(grace_s=1.0)


@pytest.fixture
def cycle_settings():
    s = MagicMock()
    s.archive_after_days = 90
    s.llm_review_enabled = False
    s.llm_review_interval_seconds = 3600
    s.check_interval_seconds = 0.05
    s.interval_seconds = 0.1
    s.promotion_min_days = 7
    return s


@pytest.mark.asyncio
async def test_two_concurrent_cycles_only_one_acquires(
    tmp_path: Path, redis_client, lock_manager, task_manager, cycle_settings
) -> None:
    with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan:
        mock_scan.side_effect = lambda **_: asyncio.sleep(
            0.5, result=CuratorReport(total_scanned=0)
        )

        kwargs = dict(
            memory_base_dir=tmp_path,
            settings=cycle_settings,
            redis_client=redis_client,
            lock_manager=lock_manager,
            task_manager=task_manager,
            l1_index=AsyncMock(),
            owner_label="test:concurrent",
        )

        results = await asyncio.gather(
            run_curator_cycle(**kwargs),
            run_curator_cycle(**kwargs),
        )

    acquired_count = sum(1 for r in results if r.acquired)
    not_acquired_count = sum(1 for r in results if not r.acquired)
    assert acquired_count == 1, f"expected exactly one winner, got {acquired_count}"
    assert not_acquired_count == 1

    remaining = task_manager.list_tasks(category="heartbeat", include_done=False)
    assert len(remaining) == 0, f"heartbeat tasks leaked: {remaining}"


@pytest.mark.asyncio
async def test_heartbeat_renews_lock_during_long_hold(
    tmp_path: Path, redis_client, lock_manager, task_manager, cycle_settings
) -> None:
    slow_scan_duration = 0.8

    async def _slow_scan(**_kwargs):
        await asyncio.sleep(slow_scan_duration)
        return CuratorReport(total_scanned=0)

    with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan:
        mock_scan.side_effect = _slow_scan

        with patch("pyclaw.core.curator._heartbeat") as mock_heartbeat:

            async def _fake_heartbeat(lm, key, token, event, interval_s=10.0, ttl_ms=30_000):
                try:
                    while True:
                        await asyncio.sleep(0.1)
                        ok = await lm.renew(key, token, ttl_ms)
                        if not ok:
                            event.set()
                            from pyclaw.storage.lock.redis import LockLostError

                            raise LockLostError(key)
                except asyncio.CancelledError:
                    raise

            mock_heartbeat.side_effect = _fake_heartbeat

            start = time.monotonic()
            report = await run_curator_cycle(
                memory_base_dir=tmp_path,
                settings=cycle_settings,
                redis_client=redis_client,
                lock_manager=lock_manager,
                task_manager=task_manager,
                l1_index=AsyncMock(),
                owner_label="test:heartbeat_renewal",
            )
            elapsed = time.monotonic() - start

    assert report.acquired is True
    assert report.error is None
    assert elapsed >= slow_scan_duration


@pytest.mark.asyncio
async def test_lock_deleted_mid_cycle_triggers_lock_lost(
    tmp_path: Path, redis_client, lock_manager, task_manager, cycle_settings
) -> None:
    for i in range(3):
        (tmp_path / f"db{i}.db").touch()

    cycle_settings.llm_review_enabled = True

    db_call_times: list[float] = []
    start = time.monotonic()

    async def _slow_review(**_kwargs):
        db_call_times.append(time.monotonic() - start)
        await asyncio.sleep(0.3)
        return 1

    with (
        patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review,
        patch(
            "pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock, return_value=True
        ),
    ):
        mock_review.side_effect = _slow_review

        async def _delete_lock_soon():
            await asyncio.sleep(0.15)
            await redis_client.delete(f"{TEST_KEY_PREFIX}{CURATOR_CYCLE_LOCK_KEY}")

        with patch("pyclaw.core.curator._heartbeat") as mock_heartbeat:

            async def _fast_check_heartbeat(lm, key, token, event, interval_s=10.0, ttl_ms=30_000):
                try:
                    while True:
                        await asyncio.sleep(0.05)
                        ok = await lm.renew(key, token, ttl_ms)
                        if not ok:
                            event.set()
                            from pyclaw.storage.lock.redis import LockLostError

                            raise LockLostError(key)
                except asyncio.CancelledError:
                    raise

            mock_heartbeat.side_effect = _fast_check_heartbeat

            deleter = asyncio.create_task(_delete_lock_soon())
            report = await run_curator_cycle(
                memory_base_dir=tmp_path,
                settings=cycle_settings,
                redis_client=redis_client,
                lock_manager=lock_manager,
                task_manager=task_manager,
                l1_index=AsyncMock(),
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
                force_review=True,
                owner_label="test:lock_lost",
            )
            await deleter

    assert report.acquired is True
    assert report.error == "lock_lost"

    raw_last_run = await redis_client.get(CURATOR_LAST_RUN_KEY)
    raw_llm_review = await redis_client.get(CURATOR_LLM_REVIEW_KEY)
    assert raw_last_run is None
    assert raw_llm_review is None


@pytest.mark.asyncio
async def test_manual_trigger_vs_timed_loop_mutual_exclusion(
    tmp_path: Path, redis_client, lock_manager, task_manager, cycle_settings
) -> None:
    with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan:
        mock_scan.side_effect = lambda **_: asyncio.sleep(
            0.5, result=CuratorReport(total_scanned=0)
        )

        await redis_client.set(CURATOR_LAST_RUN_KEY, str(time.time() - 1000))

        loop_task = asyncio.create_task(
            create_curator_loop(
                settings=cycle_settings,
                memory_base_dir=tmp_path,
                redis_client=redis_client,
                l1_index=AsyncMock(),
                lock_manager=lock_manager,
                task_manager=task_manager,
            )
        )

        await asyncio.sleep(0.15)

        manual_report = await run_curator_cycle(
            memory_base_dir=tmp_path,
            settings=cycle_settings,
            redis_client=redis_client,
            lock_manager=lock_manager,
            task_manager=task_manager,
            l1_index=AsyncMock(),
            mode="review_only",
            force_review=True,
            owner_label="test:manual",
        )

        loop_task.cancel()
        try:
            await loop_task
        except asyncio.CancelledError:
            pass

    assert manual_report.acquired is False
