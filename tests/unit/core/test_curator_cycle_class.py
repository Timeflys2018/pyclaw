"""Tests for CuratorCycle class (Phase C1, C1b).

CuratorCycle replaces the 700-line run_curator_cycle function with a class
whose cycle-scoped state is explicit fields rather than closure variables.

Audit-trail anchors: C1, C1b map to tasks.md.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.core.curator import CuratorReport
from pyclaw.core.curator_cycle import CuratorCycle
from pyclaw.core.curator_state import CuratorStateStore
from pyclaw.infra.task_manager import TaskManager
from pyclaw.storage.lock.redis import LockAcquireError, LockLostError


@pytest.fixture
def cycle_deps(tmp_path: Path):
    memory_base_dir = tmp_path / "memory"
    memory_base_dir.mkdir()

    settings = MagicMock()
    settings.archive_after_days = 90
    settings.llm_review_enabled = False
    settings.llm_review_interval_seconds = 3600
    settings.llm_review_max_batch = 20
    settings.llm_review_actions = ["promote", "archive"]
    settings.llm_review_model = "gpt-4o-mini"
    settings.graduation_enabled = False

    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock(return_value=True)

    lock_manager = AsyncMock()
    lock_manager.acquire = AsyncMock(return_value="tok_test")
    lock_manager.release = AsyncMock(return_value=True)
    lock_manager.renew = AsyncMock(return_value=True)

    tm = TaskManager()

    l1_index = AsyncMock()

    return {
        "memory_base_dir": memory_base_dir,
        "settings": settings,
        "state_store": CuratorStateStore(redis),
        "lock_manager": lock_manager,
        "task_manager": tm,
        "l1_index": l1_index,
        "redis": redis,
    }


def _build_cycle(cycle_deps, **overrides):
    kwargs = dict(
        memory_base_dir=cycle_deps["memory_base_dir"],
        settings=cycle_deps["settings"],
        state_store=cycle_deps["state_store"],
        lock_manager=cycle_deps["lock_manager"],
        task_manager=cycle_deps["task_manager"],
        l1_index=cycle_deps["l1_index"],
    )
    kwargs.update(overrides)
    return CuratorCycle(**kwargs)


class TestCuratorCycleConstruction:
    """C1.1: CuratorCycle constructs with required dependencies."""

    def test_constructs_with_minimal_kwargs(self, cycle_deps) -> None:
        cycle = _build_cycle(cycle_deps)
        assert cycle is not None

    def test_defaults_mode_to_scan_and_review(self, cycle_deps) -> None:
        cycle = _build_cycle(cycle_deps)
        assert cycle._mode == "scan_and_review"

    def test_force_review_defaults_false(self, cycle_deps) -> None:
        cycle = _build_cycle(cycle_deps)
        assert cycle._force_review is False

    def test_owner_label_defaults_timed(self, cycle_deps) -> None:
        cycle = _build_cycle(cycle_deps)
        assert cycle._owner_label == "timed"

    def test_initial_state_clean(self, cycle_deps) -> None:
        cycle = _build_cycle(cycle_deps)
        assert cycle._scan_report is None
        assert cycle._review_outcomes == []
        assert cycle._error is None
        assert cycle._unexpected_exception is False
        assert cycle._executed is False


class TestCuratorCycleLockAcquireFailure:
    """C1.1: LockAcquireError -> CycleReport(acquired=False)."""

    @pytest.mark.asyncio
    async def test_lock_busy_returns_acquired_false(self, cycle_deps) -> None:
        cycle_deps["lock_manager"].acquire.side_effect = LockAcquireError(
            "pyclaw:curator:cycle",
        )
        cycle = _build_cycle(cycle_deps)
        report = await cycle.execute()
        assert report.acquired is False
        assert report.error is None
        assert report.scan_report is None
        assert report.unexpected_exception is False


class TestCuratorCycleExecuteOnce:
    """C1.1: execute() is not reusable (fails on second call)."""

    @pytest.mark.asyncio
    async def test_second_execute_raises(self, cycle_deps, tmp_path: Path) -> None:
        cycle = _build_cycle(cycle_deps)
        with patch(
            "pyclaw.core.curator.run_curator_scan",
            new_callable=AsyncMock,
            return_value=CuratorReport(total_scanned=0),
        ):
            await cycle.execute()
        with pytest.raises(RuntimeError, match="not reusable"):
            await cycle.execute()


class TestCuratorCycleScanOnly:
    """C1.1: scan_and_review mode without LLM review finishes cleanly."""

    @pytest.mark.asyncio
    async def test_scan_report_populated(self, cycle_deps) -> None:
        cycle = _build_cycle(cycle_deps)
        fake_report = CuratorReport(total_scanned=3, total_archived=1)
        with patch(
            "pyclaw.core.curator.run_curator_scan",
            new_callable=AsyncMock,
            return_value=fake_report,
        ):
            report = await cycle.execute()
        assert report.acquired is True
        assert report.scan_report is fake_report
        assert report.review_action_count == 0
        assert report.unexpected_exception is False

    @pytest.mark.asyncio
    async def test_state_store_mark_scan_completed_called(self, cycle_deps) -> None:
        cycle = _build_cycle(cycle_deps)
        with patch(
            "pyclaw.core.curator.run_curator_scan",
            new_callable=AsyncMock,
            return_value=CuratorReport(total_scanned=0),
        ):
            await cycle.execute()
        set_calls = [c.args for c in cycle_deps["redis"].set.await_args_list]
        keys = [c[0] for c in set_calls]
        assert "pyclaw:curator:last_run_at" in keys


class TestCuratorCycleReviewOnlyMode:
    """C1.1: review_only mode skips scan."""

    @pytest.mark.asyncio
    async def test_scan_not_called_in_review_only(self, cycle_deps) -> None:
        cycle = _build_cycle(cycle_deps, mode="review_only")
        with patch(
            "pyclaw.core.curator.run_curator_scan",
            new_callable=AsyncMock,
        ) as mock_scan:
            await cycle.execute()
        mock_scan.assert_not_called()


class TestCuratorCycleLockLost:
    """C1.1: LockLostError during critical section -> error='lock_lost'."""

    @pytest.mark.asyncio
    async def test_scan_lock_lost_sets_error(self, cycle_deps) -> None:
        cycle = _build_cycle(cycle_deps)
        with patch(
            "pyclaw.core.curator.run_curator_scan",
            new_callable=AsyncMock,
            side_effect=LockLostError("pyclaw:curator:cycle"),
        ):
            report = await cycle.execute()
        assert report.acquired is True
        assert report.error == "lock_lost"
        assert report.unexpected_exception is False


class TestCuratorCycleUnexpectedException:
    """C1b: non-LockLost exception is swallowed, flag set."""

    @pytest.mark.asyncio
    async def test_scan_unexpected_exception_sets_flag(
        self,
        cycle_deps,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        cycle = _build_cycle(cycle_deps)
        with (
            caplog.at_level(logging.ERROR, logger="pyclaw.core.curator_cycle"),
            patch(
                "pyclaw.core.curator.run_curator_scan",
                new_callable=AsyncMock,
                side_effect=RuntimeError("scan broke"),
            ),
        ):
            report = await cycle.execute()

        assert report.acquired is True
        assert report.error is None
        assert report.unexpected_exception is True
        assert report.scan_report is None
        assert any("unexpected" in rec.message.lower() for rec in caplog.records)

    @pytest.mark.asyncio
    async def test_lock_acquire_error_does_not_set_unexpected_flag(
        self,
        cycle_deps,
    ) -> None:
        cycle_deps["lock_manager"].acquire.side_effect = LockAcquireError("k")
        cycle = _build_cycle(cycle_deps)
        report = await cycle.execute()
        assert report.unexpected_exception is False

    @pytest.mark.asyncio
    async def test_lock_lost_does_not_set_unexpected_flag(
        self,
        cycle_deps,
    ) -> None:
        cycle = _build_cycle(cycle_deps)
        with patch(
            "pyclaw.core.curator.run_curator_scan",
            new_callable=AsyncMock,
            side_effect=LockLostError("k"),
        ):
            report = await cycle.execute()
        assert report.error == "lock_lost"
        assert report.unexpected_exception is False
