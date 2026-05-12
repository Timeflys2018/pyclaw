"""CuratorCycle: one-cycle execution as a class with explicit state fields.

Replaces the 700-line ``run_curator_cycle`` function. The legacy function
is preserved as a thin wrapper (Phase C3) to avoid breaking the 20+ call
sites that expect it; new code should construct :class:`CuratorCycle`
directly.

This class still uses the inline heartbeat pattern from ``curator._heartbeat``.
Phase D replaces that with :class:`DistributedMutex`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pyclaw.core import curator as _curator
from pyclaw.core.curator import (
    CURATOR_CYCLE_LOCK_KEY,
    CuratorReport,
    CycleError,
    CycleReport,
    ReviewOutcome,
)
from pyclaw.storage.lock.redis import LockAcquireError, LockLostError

if TYPE_CHECKING:
    from pyclaw.core.curator_state import CuratorStateStore
    from pyclaw.infra.task_manager import TaskManager
    from pyclaw.storage.lock.redis import RedisLockManager

logger = logging.getLogger(__name__)

CycleMode = Literal["scan_and_review", "review_only"]


class CuratorCycle:
    """One curator cycle execution with cycle-scoped state.

    Lifecycle: construct -> ``await execute()`` -> read ``CycleReport``.
    Not thread-safe. Not reusable — each cycle requires a fresh instance.
    """

    def __init__(
        self,
        *,
        memory_base_dir: Path,
        settings: Any,
        state_store: "CuratorStateStore",
        lock_manager: "RedisLockManager",
        task_manager: "TaskManager",
        l1_index: Any,
        workspace_base_dir: Path | None = None,
        llm_client: Any = None,
        mode: CycleMode = "scan_and_review",
        force_review: bool = False,
        owner_label: str = "timed",
    ) -> None:
        self._memory_base_dir = memory_base_dir
        self._settings = settings
        self._state_store = state_store
        self._lock_manager = lock_manager
        self._task_manager = task_manager
        self._l1_index = l1_index
        self._workspace_base_dir = workspace_base_dir
        self._llm_client = llm_client
        self._mode = mode
        self._force_review = force_review
        self._owner_label = owner_label

        self._scan_report: CuratorReport | None = None
        self._review_outcomes: list[ReviewOutcome] = []
        self._error: CycleError = None
        self._unexpected_exception: bool = False
        self._review_completed_full_traversal: bool = False
        self._executed: bool = False

    async def execute(self) -> CycleReport:
        if self._executed:
            raise RuntimeError(
                "CuratorCycle is not reusable; create a new instance per cycle",
            )
        self._executed = True

        try:
            token = await self._lock_manager.acquire(CURATOR_CYCLE_LOCK_KEY)
        except LockAcquireError:
            logger.debug(
                "curator cycle lock busy owner=%s",
                self._owner_label,
            )
            return CycleReport(acquired=False)

        logger.info(
            "curator cycle acquired lock owner=%s mode=%s force=%s",
            self._owner_label, self._mode, self._force_review,
        )

        lock_lost_event = asyncio.Event()
        heartbeat_task_id: str | None = None
        try:
            heartbeat_task_id = self._task_manager.spawn(
                "curator-heartbeat",
                _curator._heartbeat(
                    self._lock_manager,
                    CURATOR_CYCLE_LOCK_KEY,
                    token,
                    lock_lost_event,
                ),
                category="heartbeat",
            )
        except BaseException:
            try:
                await self._lock_manager.release(CURATOR_CYCLE_LOCK_KEY, token)
            except Exception:
                logger.debug("release after spawn failure errored", exc_info=True)
            raise

        await asyncio.sleep(0)
        check_alive = self._make_check_alive(heartbeat_task_id, lock_lost_event)

        try:
            await self._run_critical_section(check_alive)
        finally:
            try:
                await self._task_manager.cancel(heartbeat_task_id)
            except Exception:
                logger.debug(
                    "heartbeat cancel errored (benign) owner=%s",
                    self._owner_label, exc_info=True,
                )
            self._consume_heartbeat_exception(heartbeat_task_id)
            try:
                await self._lock_manager.release(CURATOR_CYCLE_LOCK_KEY, token)
            except Exception:
                logger.debug(
                    "lock release errored (benign) owner=%s",
                    self._owner_label, exc_info=True,
                )

        return self._build_report()

    async def _run_critical_section(self, check_alive: Callable[[], None]) -> None:
        try:
            if self._mode == "scan_and_review":
                await self._run_scan(check_alive)
            await self._run_review_if_permitted(check_alive)
            await self._mark_completion()
        except LockLostError:
            self._error = "lock_lost"
            logger.warning(
                "curator cycle lock loss owner=%s completed_dbs=%d",
                self._owner_label, len(self._review_outcomes),
            )
        except Exception:
            logger.exception(
                "curator cycle unexpected exception owner=%s",
                self._owner_label,
            )
            self._unexpected_exception = True

    async def _run_scan(self, check_alive: Callable[[], None]) -> None:
        check_alive()
        self._scan_report = await _curator.run_curator_scan(
            memory_base_dir=self._memory_base_dir,
            archive_days=self._settings.archive_after_days,
            l1_index=self._l1_index,
            workspace_base_dir=self._workspace_base_dir,
            settings=self._settings,
            check_alive=check_alive,
        )
        log_fn = logger.info if (
            self._scan_report.total_archived > 0
            or self._scan_report.total_graduated > 0
        ) else logger.debug
        log_fn(
            "curator scan complete scanned=%d archived=%d graduated=%d errors=%d owner=%s",
            self._scan_report.total_scanned,
            self._scan_report.total_archived,
            self._scan_report.total_graduated,
            len(self._scan_report.errors),
            self._owner_label,
        )
        for err in self._scan_report.errors[:5]:
            logger.warning(
                "curator scan error %s owner=%s", err, self._owner_label,
            )

    async def _run_review_if_permitted(
        self, check_alive: Callable[[], None],
    ) -> None:
        if not getattr(self._settings, "llm_review_enabled", True):
            self._error = "review_skipped_interval"
            return
        if self._llm_client is None or self._workspace_base_dir is None:
            return

        should = self._force_review or await _curator.should_run_llm_review(
            self._settings, self._state_store,
        )
        if not should:
            self._error = "review_skipped_interval"
            return

        db_files = sorted(self._memory_base_dir.glob("*.db"))
        if not db_files:
            return

        completed = 0
        check_alive()
        for db_file in db_files:
            check_alive()
            try:
                outcome = await _curator.run_llm_review(
                    db_file=db_file,
                    settings=self._settings,
                    llm_client=self._llm_client,
                    l1_index=self._l1_index,
                    workspace_base_dir=self._workspace_base_dir,
                    check_alive=check_alive,
                )
                self._review_outcomes.append(outcome)
                if outcome.total_actions > 0:
                    logger.info(
                        "curator llm review actions=%d db=%s owner=%s",
                        outcome.total_actions, db_file.name, self._owner_label,
                    )
            except LockLostError:
                raise
            except Exception:
                logger.warning(
                    "curator llm review failed db=%s owner=%s",
                    db_file.name, self._owner_label, exc_info=True,
                )
            completed += 1

        if completed == len(db_files):
            self._review_completed_full_traversal = True

    async def _mark_completion(self) -> None:
        if self._error == "lock_lost":
            return
        if self._mode == "scan_and_review":
            await self._state_store.mark_scan_completed()
        if self._review_completed_full_traversal:
            await self._state_store.mark_review_fully_completed()

    def _make_check_alive(
        self,
        heartbeat_task_id: str,
        lock_lost_event: asyncio.Event,
    ) -> Callable[[], None]:
        """Return a closure that raises LockLostError if the heartbeat has lost
        the lock or the heartbeat task has terminated unexpectedly.

        Fail-closed on a missing handle (behavior change matching
        DistributedMutex semantics; see Phase D).
        """
        task_manager = self._task_manager

        def _heartbeat_done() -> bool:
            state = task_manager.get_state(heartbeat_task_id)
            if state is None:
                return True
            return state != "running"

        def check_alive() -> None:
            if lock_lost_event.is_set() or _heartbeat_done():
                raise LockLostError(CURATOR_CYCLE_LOCK_KEY)

        return check_alive

    def _consume_heartbeat_exception(self, heartbeat_task_id: str) -> None:
        """Retrieve any stored heartbeat exception so asyncio does not log
        ``Task exception was never retrieved`` at GC. Exceptions have
        already been logged inside ``_heartbeat`` itself.
        """
        handle = self._task_manager._tasks.get(heartbeat_task_id)
        if handle is None:
            return
        task = handle.asyncio_task
        if not task.done() or task.cancelled():
            return
        try:
            task.exception()
        except Exception:
            pass

    def _build_report(self) -> CycleReport:
        action_count = sum(
            o.total_actions for o in self._review_outcomes
        )
        return CycleReport(
            acquired=True,
            scan_report=self._scan_report,
            review_action_count=action_count,
            error=self._error,
            unexpected_exception=self._unexpected_exception,
        )
