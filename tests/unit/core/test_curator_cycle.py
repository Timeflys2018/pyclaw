"""Tests for run_curator_cycle and related types (Phase A3+B)."""

from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from pyclaw.storage.lock.redis import LockAcquireError, LockLostError


# ─── A3: CycleReport dataclass + CycleError type ────────────────────────────


class TestCycleReport:

    def test_basic_construction(self) -> None:
        from pyclaw.core.curator import CycleReport

        report = CycleReport(acquired=True, scan_report=None, review_action_count=0, error=None)
        assert report.acquired is True
        assert report.scan_report is None
        assert report.review_action_count == 0
        assert report.error is None

    def test_error_lock_lost(self) -> None:
        from pyclaw.core.curator import CycleReport

        report = CycleReport(acquired=True, error="lock_lost")
        assert report.error == "lock_lost"

    def test_error_review_skipped_interval(self) -> None:
        from pyclaw.core.curator import CycleReport

        report = CycleReport(acquired=True, error="review_skipped_interval")
        assert report.error == "review_skipped_interval"

    def test_error_memory_base_dir_missing(self) -> None:
        from pyclaw.core.curator import CycleReport

        report = CycleReport(acquired=True, error="memory_base_dir_missing")
        assert report.error == "memory_base_dir_missing"

    def test_defaults(self) -> None:
        from pyclaw.core.curator import CycleReport

        report = CycleReport(acquired=False)
        assert report.scan_report is None
        assert report.review_action_count == 0
        assert report.error is None


class TestCuratorCycleLockKey:

    def test_cycle_lock_key_value(self) -> None:
        from pyclaw.core.curator import CURATOR_CYCLE_LOCK_KEY

        assert CURATOR_CYCLE_LOCK_KEY == "curator:cycle"

    def test_old_lock_key_still_importable(self) -> None:
        from pyclaw.core.curator import CURATOR_LOCK_KEY

        assert CURATOR_LOCK_KEY == "pyclaw:curator:lock"


# ─── B1: run_curator_cycle signature + scan_and_review ───────────────────────


class TestRunCuratorCycleSignature:

    def test_all_params_keyword_only(self) -> None:
        from pyclaw.core.curator import run_curator_cycle

        sig = inspect.signature(run_curator_cycle)
        params = list(sig.parameters.values())
        for p in params:
            assert p.kind == inspect.Parameter.KEYWORD_ONLY, (
                f"param {p.name!r} should be keyword-only"
            )

    def test_param_names_and_defaults(self) -> None:
        from pyclaw.core.curator import run_curator_cycle

        sig = inspect.signature(run_curator_cycle)
        names = list(sig.parameters.keys())
        expected = [
            "memory_base_dir", "settings", "redis_client", "lock_manager",
            "task_manager", "l1_index", "workspace_base_dir", "llm_client",
            "mode", "force_review", "owner_label",
        ]
        assert names == expected

        assert sig.parameters["workspace_base_dir"].default is None
        assert sig.parameters["llm_client"].default is None
        assert sig.parameters["mode"].default == "scan_and_review"
        assert sig.parameters["force_review"].default is False
        assert sig.parameters["owner_label"].default == "timed"

    def test_task_manager_is_required(self) -> None:
        from pyclaw.core.curator import run_curator_cycle

        sig = inspect.signature(run_curator_cycle)
        assert sig.parameters["task_manager"].default is inspect.Parameter.empty


@pytest.fixture
def cycle_deps(tmp_path: Path):
    memory_base_dir = tmp_path / "memory"
    memory_base_dir.mkdir()

    settings = MagicMock()
    settings.llm_review_enabled = True
    settings.llm_review_interval_seconds = 3600
    settings.archive_after_days = 90

    redis_client = AsyncMock()
    redis_client.get = AsyncMock(return_value=None)
    redis_client.set = AsyncMock(return_value=True)

    lock_manager = AsyncMock()
    lock_manager.acquire = AsyncMock(return_value="token_abc")
    lock_manager.release = AsyncMock(return_value=True)

    task_manager = MagicMock()

    def _spawn_closing(name, coro, **kwargs):
        coro.close()
        return "t000001"

    task_manager.spawn = MagicMock(side_effect=_spawn_closing)
    task_manager.cancel = AsyncMock(return_value=True)
    # Provide a _tasks dict with a mock handle for heartbeat task access
    mock_asyncio_task = MagicMock()
    mock_asyncio_task.done = MagicMock(return_value=False)
    mock_handle = MagicMock()
    mock_handle.asyncio_task = mock_asyncio_task
    task_manager._tasks = {"t000001": mock_handle}

    l1_index = AsyncMock()

    return {
        "memory_base_dir": memory_base_dir,
        "settings": settings,
        "redis_client": redis_client,
        "lock_manager": lock_manager,
        "task_manager": task_manager,
        "l1_index": l1_index,
    }


class TestRunCuratorCycleScanAndReview:

    @pytest.mark.asyncio
    async def test_scan_and_review_calls_scan_and_review(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import CuratorReport, run_curator_cycle

        # Create db files for review
        db1 = cycle_deps["memory_base_dir"] / "user1.db"
        db1.touch()
        db2 = cycle_deps["memory_base_dir"] / "user2.db"
        db2.touch()

        mock_scan_report = CuratorReport(total_scanned=2)

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_scan.return_value = mock_scan_report
            mock_review.return_value = 2
            mock_should.return_value = True

            report = await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
            )

        assert report.acquired is True
        assert report.scan_report is mock_scan_report
        mock_scan.assert_awaited_once()
        assert mock_review.await_count == 2

    @pytest.mark.asyncio
    async def test_last_run_key_updated_on_success(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import CURATOR_LAST_RUN_KEY, CuratorReport, run_curator_cycle

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_scan.return_value = CuratorReport(total_scanned=0)
            mock_should.return_value = False

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
            )

        # CURATOR_LAST_RUN_KEY set call
        set_calls = cycle_deps["redis_client"].set.call_args_list
        last_run_calls = [c for c in set_calls if c[0][0] == CURATOR_LAST_RUN_KEY]
        assert len(last_run_calls) == 1

    @pytest.mark.asyncio
    async def test_llm_review_key_updated_after_full_review(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import CURATOR_LLM_REVIEW_KEY, CuratorReport, run_curator_cycle

        db1 = cycle_deps["memory_base_dir"] / "user1.db"
        db1.touch()

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_scan.return_value = CuratorReport(total_scanned=1)
            mock_review.return_value = 1
            mock_should.return_value = True

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
            )

        set_calls = cycle_deps["redis_client"].set.call_args_list
        review_calls = [c for c in set_calls if c[0][0] == CURATOR_LLM_REVIEW_KEY]
        assert len(review_calls) == 1


# ─── B2: review_only + force_review ─────────────────────────────────────────


class TestRunCuratorCycleReviewOnly:

    @pytest.mark.asyncio
    async def test_review_only_does_not_call_scan(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import CuratorReport, run_curator_cycle

        db1 = cycle_deps["memory_base_dir"] / "user1.db"
        db1.touch()

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_scan.return_value = CuratorReport(total_scanned=0)
            mock_review.return_value = 0
            mock_should.return_value = True

            report = await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
            )

        mock_scan.assert_not_awaited()
        assert report.scan_report is None

    @pytest.mark.asyncio
    async def test_review_only_skipped_by_interval(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import run_curator_cycle

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_should.return_value = False

            report = await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
                force_review=False,
            )

        assert report.error == "review_skipped_interval"
        mock_review.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_review_only_force_bypasses_interval(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import run_curator_cycle

        db1 = cycle_deps["memory_base_dir"] / "user1.db"
        db1.touch()

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_should.return_value = False
            mock_review.return_value = 1

            report = await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
                force_review=True,
            )

        mock_review.assert_awaited_once()
        assert report.error is None

    @pytest.mark.asyncio
    async def test_force_review_but_llm_disabled_still_skips(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import run_curator_cycle

        cycle_deps["settings"].llm_review_enabled = False

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock):

            report = await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
                force_review=True,
            )

        mock_review.assert_not_awaited()
        assert report.error == "review_skipped_interval"

    @pytest.mark.asyncio
    async def test_review_only_no_last_run_key_update(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import CURATOR_LAST_RUN_KEY, run_curator_cycle

        db1 = cycle_deps["memory_base_dir"] / "user1.db"
        db1.touch()

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_should.return_value = True
            mock_review.return_value = 0

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
            )

        set_calls = cycle_deps["redis_client"].set.call_args_list
        last_run_calls = [c for c in set_calls if c[0][0] == CURATOR_LAST_RUN_KEY]
        assert len(last_run_calls) == 0

    @pytest.mark.asyncio
    async def test_review_only_llm_review_key_updated(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import CURATOR_LLM_REVIEW_KEY, run_curator_cycle

        db1 = cycle_deps["memory_base_dir"] / "user1.db"
        db1.touch()

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_should.return_value = True
            mock_review.return_value = 1

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
            )

        set_calls = cycle_deps["redis_client"].set.call_args_list
        review_calls = [c for c in set_calls if c[0][0] == CURATOR_LLM_REVIEW_KEY]
        assert len(review_calls) == 1


# ─── B2.3: Edge cases ───────────────────────────────────────────────────────


class TestRunCuratorCycleReviewEdgeCases:

    @pytest.mark.asyncio
    async def test_empty_db_glob_no_review_key_update(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import CURATOR_LLM_REVIEW_KEY, run_curator_cycle

        # No .db files in memory_base_dir

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_should.return_value = True
            mock_review.return_value = 0

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
                force_review=True,
            )

        set_calls = cycle_deps["redis_client"].set.call_args_list
        review_calls = [c for c in set_calls if c[0][0] == CURATOR_LLM_REVIEW_KEY]
        assert len(review_calls) == 0
        mock_review.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_internal_exception_silenced_review_continues(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import CURATOR_LLM_REVIEW_KEY, run_curator_cycle

        for i in range(5):
            (cycle_deps["memory_base_dir"] / f"db{i}.db").touch()

        call_count = 0

        async def _review_side_effect(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("internal explosion")
            return 1

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock) as mock_should:
            mock_should.return_value = True
            mock_review.side_effect = _review_side_effect

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
                force_review=True,
            )

        # All 5 dbs processed (one threw, but silenced)
        assert mock_review.await_count == 5
        # Key IS updated because full traversal completed
        set_calls = cycle_deps["redis_client"].set.call_args_list
        review_calls = [c for c in set_calls if c[0][0] == CURATOR_LLM_REVIEW_KEY]
        assert len(review_calls) == 1


# ─── B3: acquire fail ────────────────────────────────────────────────────────


class TestRunCuratorCycleAcquireFail:

    @pytest.mark.asyncio
    async def test_acquire_fail_returns_not_acquired(self, cycle_deps) -> None:
        from pyclaw.core.curator import run_curator_cycle

        cycle_deps["lock_manager"].acquire = AsyncMock(
            side_effect=LockAcquireError("pyclaw:curator:cycle")
        )

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review:

            report = await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
            )

        assert report.acquired is False
        assert cycle_deps["task_manager"].spawn.call_count == 0
        mock_scan.assert_not_awaited()
        mock_review.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_acquire_fail_no_redis_timestamps(self, cycle_deps) -> None:
        from pyclaw.core.curator import run_curator_cycle

        cycle_deps["lock_manager"].acquire = AsyncMock(
            side_effect=LockAcquireError("pyclaw:curator:cycle")
        )

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock):

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
            )

        cycle_deps["redis_client"].set.assert_not_awaited()


# ─── B4: heartbeat spawn + release ordering ──────────────────────────────────


class TestRunCuratorCycleHeartbeat:

    @pytest.mark.asyncio
    async def test_heartbeat_spawned_with_correct_params(self, cycle_deps) -> None:
        from pyclaw.core.curator import CuratorReport, run_curator_cycle

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock, return_value=False):
            mock_scan.return_value = CuratorReport(total_scanned=0)

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
            )

        spawn_call = cycle_deps["task_manager"].spawn.call_args
        assert spawn_call[0][0] == "curator-heartbeat"
        assert asyncio.iscoroutine(spawn_call[0][1])
        # Clean up the unawaited coroutine
        spawn_call[0][1].close()
        assert spawn_call[1] == {"category": "heartbeat"}

    @pytest.mark.asyncio
    async def test_spawn_has_no_owner_kwarg(self, cycle_deps) -> None:
        from pyclaw.core.curator import CuratorReport, run_curator_cycle

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock, return_value=False):
            mock_scan.return_value = CuratorReport(total_scanned=0)

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
            )

        spawn_kwargs = cycle_deps["task_manager"].spawn.call_args[1]
        assert "owner" not in spawn_kwargs

    @pytest.mark.asyncio
    async def test_cancel_then_release_ordering(self, cycle_deps) -> None:
        from pyclaw.core.curator import CuratorReport, run_curator_cycle

        call_order = []
        original_cancel = cycle_deps["task_manager"].cancel

        async def track_cancel(*args, **kwargs):
            call_order.append("cancel")
            return await original_cancel(*args, **kwargs)

        async def track_release(*args, **kwargs):
            call_order.append("release")
            return True

        cycle_deps["task_manager"].cancel = track_cancel
        cycle_deps["lock_manager"].release = track_release

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock, return_value=False):
            mock_scan.return_value = CuratorReport(total_scanned=0)

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
            )

        assert call_order == ["cancel", "release"]

    @pytest.mark.asyncio
    async def test_cancel_idempotent_when_heartbeat_already_done(self, cycle_deps) -> None:
        from pyclaw.core.curator import CuratorReport, run_curator_cycle

        # Heartbeat already done (simulating it exited early)
        cycle_deps["task_manager"]._tasks["t000001"].asyncio_task.done.return_value = True
        cycle_deps["task_manager"].cancel = AsyncMock(return_value=False)

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock, return_value=False):
            mock_scan.return_value = CuratorReport(total_scanned=0)

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
            )

        # cancel still called (idempotent, doesn't raise)
        cycle_deps["task_manager"].cancel.assert_awaited_once()


# ─── B5: LockLostError propagation double fail-safe ──────────────────────────


class TestRunCuratorCycleLockLost:

    @pytest.mark.asyncio
    async def test_event_set_aborts_cycle(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import CURATOR_LAST_RUN_KEY, CURATOR_LLM_REVIEW_KEY, run_curator_cycle

        db1 = cycle_deps["memory_base_dir"] / "db1.db"
        db1.touch()
        db2 = cycle_deps["memory_base_dir"] / "db2.db"
        db2.touch()

        review_call_count = 0

        async def _review_side_effect(**kwargs):
            nonlocal review_call_count
            review_call_count += 1
            if review_call_count == 1:
                # After first db succeeds, simulate heartbeat setting event
                # We need to set it via the event that run_curator_cycle creates internally
                # So we mock _heartbeat to set the event immediately
                return 3
            return 1

        # Make the heartbeat task appear done after first review
        call_count = [0]
        original_done = cycle_deps["task_manager"]._tasks["t000001"].asyncio_task.done

        def _done_after_first():
            call_count[0] += 1
            # First few calls during spawn/setup return False, after first review return True
            if call_count[0] > 2:
                return True
            return False

        cycle_deps["task_manager"]._tasks["t000001"].asyncio_task.done = _done_after_first

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock, return_value=True):
            mock_review.side_effect = _review_side_effect

            report = await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
                force_review=True,
            )

        assert report.acquired is True
        assert report.error == "lock_lost"
        assert report.review_action_count == 3  # first db's actions preserved
        # Only first db reviewed (second aborted)
        assert mock_review.await_count == 1

        # Timestamps NOT updated
        set_calls = cycle_deps["redis_client"].set.call_args_list
        last_run_calls = [c for c in set_calls if c[0][0] == CURATOR_LAST_RUN_KEY]
        review_calls = [c for c in set_calls if c[0][0] == CURATOR_LLM_REVIEW_KEY]
        assert len(last_run_calls) == 0
        assert len(review_calls) == 0

    @pytest.mark.asyncio
    async def test_heartbeat_task_done_without_event_aborts(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import run_curator_cycle

        db1 = cycle_deps["memory_base_dir"] / "db1.db"
        db1.touch()
        db2 = cycle_deps["memory_base_dir"] / "db2.db"
        db2.touch()

        review_call_count = 0

        async def _review_side_effect(**kwargs):
            nonlocal review_call_count
            review_call_count += 1
            return 2

        # Heartbeat task done from the start (mysterious crash)
        cycle_deps["task_manager"]._tasks["t000001"].asyncio_task.done = MagicMock(return_value=True)

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock) as mock_review, \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock, return_value=True):
            mock_review.side_effect = _review_side_effect

            report = await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
                force_review=True,
            )

        assert report.error == "lock_lost"
        # No reviews ran (aborted at first db boundary check)
        assert mock_review.await_count == 0

    @pytest.mark.asyncio
    async def test_lock_lost_cancel_and_release_still_called(self, cycle_deps, tmp_path) -> None:
        from pyclaw.core.curator import run_curator_cycle

        db1 = cycle_deps["memory_base_dir"] / "db1.db"
        db1.touch()

        # Heartbeat immediately done
        cycle_deps["task_manager"]._tasks["t000001"].asyncio_task.done = MagicMock(return_value=True)

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock, return_value=True):

            await run_curator_cycle(
                memory_base_dir=cycle_deps["memory_base_dir"],
                settings=cycle_deps["settings"],
                redis_client=cycle_deps["redis_client"],
                lock_manager=cycle_deps["lock_manager"],
                task_manager=cycle_deps["task_manager"],
                l1_index=cycle_deps["l1_index"],
                workspace_base_dir=tmp_path,
                llm_client=AsyncMock(),
                mode="review_only",
                force_review=True,
            )

        # Cancel still called (idempotent)
        cycle_deps["task_manager"].cancel.assert_awaited_once()
        # Release still called
        cycle_deps["lock_manager"].release.assert_awaited_once()


# ─── B6: owner_label log propagation ─────────────────────────────────────────


class TestRunCuratorCycleOwnerLabel:

    @pytest.mark.asyncio
    async def test_owner_label_in_log_extras(self, cycle_deps, caplog) -> None:
        import logging

        from pyclaw.core.curator import CuratorReport, run_curator_cycle

        with patch("pyclaw.core.curator.run_curator_scan", new_callable=AsyncMock) as mock_scan, \
             patch("pyclaw.core.curator.run_llm_review", new_callable=AsyncMock), \
             patch("pyclaw.core.curator.should_run_llm_review", new_callable=AsyncMock, return_value=False):
            mock_scan.return_value = CuratorReport(total_scanned=0)

            with caplog.at_level(logging.DEBUG, logger="pyclaw.core.curator"):
                await run_curator_cycle(
                    memory_base_dir=cycle_deps["memory_base_dir"],
                    settings=cycle_deps["settings"],
                    redis_client=cycle_deps["redis_client"],
                    lock_manager=cycle_deps["lock_manager"],
                    task_manager=cycle_deps["task_manager"],
                    l1_index=cycle_deps["l1_index"],
                    owner_label="manual:web:user_x",
                )

        # Check that at least one log record has owner_label in extras or message
        found = any(
            getattr(record, "owner_label", None) == "manual:web:user_x"
            or "manual:web:user_x" in record.getMessage()
            for record in caplog.records
            if record.name == "pyclaw.core.curator"
        )
        assert found, "owner_label not found in any log record"
