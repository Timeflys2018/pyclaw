"""Tests for create_curator_loop scheduler behavior (Phase C)."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.core.curator import CuratorReport, CycleReport


@pytest.fixture
def loop_deps(tmp_path: Path):
    settings = MagicMock()
    settings.check_interval_seconds = 0.01
    settings.interval_seconds = 3600
    settings.archive_after_days = 90
    settings.llm_review_enabled = False
    settings.llm_review_interval_seconds = 3600
    settings.promotion_min_days = 7

    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value=None)
    redis_client.set = AsyncMock(return_value=True)

    lock_manager = AsyncMock()
    task_manager = MagicMock()

    def _spawn_closing(name, coro, **kwargs):
        coro.close()
        return "t000001"

    task_manager.spawn = MagicMock(side_effect=_spawn_closing)
    task_manager.cancel = AsyncMock(return_value=True)
    mock_asyncio_task = MagicMock()
    mock_asyncio_task.done = MagicMock(return_value=False)
    mock_handle = MagicMock()
    mock_handle.asyncio_task = mock_asyncio_task
    task_manager._tasks = {"t000001": mock_handle}

    l1_index = AsyncMock()

    return {
        "settings": settings,
        "memory_base_dir": tmp_path,
        "redis_client": redis_client,
        "lock_manager": lock_manager,
        "task_manager": task_manager,
        "l1_index": l1_index,
    }


class TestCuratorLoopSignature:
    def test_loop_requires_lock_manager_and_task_manager(self) -> None:
        import inspect

        from pyclaw.core.curator import create_curator_loop

        sig = inspect.signature(create_curator_loop)
        names = list(sig.parameters.keys())
        assert "lock_manager" in names
        assert "task_manager" in names


class TestCuratorLoopIntervalGate:
    @pytest.mark.asyncio
    async def test_interval_not_reached_skips_cycle(self, loop_deps) -> None:
        from pyclaw.core.curator import create_curator_loop

        recent_ts = str(time.time())
        loop_deps["redis_client"].get = AsyncMock(return_value=recent_ts)

        with patch("pyclaw.core.curator.run_curator_cycle", new_callable=AsyncMock) as mock_cycle:
            task = asyncio.create_task(create_curator_loop(**loop_deps))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_cycle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_interval_reached_invokes_cycle(self, loop_deps) -> None:
        from pyclaw.core.curator import create_curator_loop

        old_ts = str(time.time() - 7200)
        loop_deps["redis_client"].get = AsyncMock(return_value=old_ts)

        with patch("pyclaw.core.curator.run_curator_cycle", new_callable=AsyncMock) as mock_cycle:
            mock_cycle.return_value = CycleReport(acquired=True, scan_report=CuratorReport())

            task = asyncio.create_task(create_curator_loop(**loop_deps))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_cycle.assert_awaited()
        call_kwargs = mock_cycle.call_args_list[0][1]
        assert call_kwargs["mode"] == "scan_and_review"
        assert call_kwargs["force_review"] is False
        assert call_kwargs["owner_label"] == "timed"
        assert call_kwargs["lock_manager"] is loop_deps["lock_manager"]
        assert call_kwargs["task_manager"] is loop_deps["task_manager"]


class TestCuratorLoopCycleResults:
    @pytest.mark.asyncio
    async def test_acquired_false_continues_without_writes(self, loop_deps) -> None:
        from pyclaw.core.curator import create_curator_loop

        loop_deps["redis_client"].get = AsyncMock(return_value=str(time.time() - 7200))

        with patch("pyclaw.core.curator.run_curator_cycle", new_callable=AsyncMock) as mock_cycle:
            mock_cycle.return_value = CycleReport(acquired=False)

            task = asyncio.create_task(create_curator_loop(**loop_deps))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        mock_cycle.assert_awaited()

    @pytest.mark.asyncio
    async def test_lock_lost_error_logged_continues(self, loop_deps, caplog) -> None:
        import logging

        from pyclaw.core.curator import create_curator_loop

        loop_deps["redis_client"].get = AsyncMock(return_value=str(time.time() - 7200))

        with patch("pyclaw.core.curator.run_curator_cycle", new_callable=AsyncMock) as mock_cycle:
            mock_cycle.return_value = CycleReport(acquired=True, error="lock_lost")

            with caplog.at_level(logging.DEBUG, logger="pyclaw.core.curator"):
                task = asyncio.create_task(create_curator_loop(**loop_deps))
                await asyncio.sleep(0.05)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        assert any(
            "lock" in record.getMessage().lower()
            for record in caplog.records
            if record.name == "pyclaw.core.curator"
        )


class TestCuratorLoopCancellation:
    @pytest.mark.asyncio
    async def test_cancellation_clean_exit(self, loop_deps) -> None:
        from pyclaw.core.curator import create_curator_loop

        task = asyncio.create_task(create_curator_loop(**loop_deps))
        await asyncio.sleep(0.02)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert task.cancelled() or task.done()


class TestCuratorLoopStartupLog:
    @pytest.mark.asyncio
    async def test_startup_log_includes_cycle_key(self, loop_deps, caplog) -> None:
        import logging

        from pyclaw.core.curator import create_curator_loop

        with caplog.at_level(logging.INFO, logger="pyclaw.core.curator"):
            task = asyncio.create_task(create_curator_loop(**loop_deps))
            await asyncio.sleep(0.02)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        found = any(
            "RedisLockManager" in record.getMessage()
            and "pyclaw:curator:cycle" in record.getMessage()
            for record in caplog.records
            if record.name == "pyclaw.core.curator"
        )
        assert found, (
            "expected startup log line mentioning RedisLockManager and pyclaw:curator:cycle"
        )
