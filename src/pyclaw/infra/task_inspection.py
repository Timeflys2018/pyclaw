"""Pure task-inspection ops shared by CLI and Chat command handlers."""

from __future__ import annotations

from pyclaw.infra.task_manager import TaskInfo, TaskManager


def list_for_owner(task_manager: TaskManager | None, *, owner: str) -> list[TaskInfo]:
    if task_manager is None:
        raise ValueError("task_manager is None")
    return task_manager.list_tasks(owner=owner)


def list_all(task_manager: TaskManager | None, *, include_done: bool = False) -> list[TaskInfo]:
    if task_manager is None:
        raise ValueError("task_manager is None")
    return task_manager.list_tasks(include_done=include_done)


def describe(task_manager: TaskManager | None, task_id: str) -> TaskInfo | None:
    if task_manager is None:
        raise ValueError("task_manager is None")
    for info in task_manager.list_tasks(include_done=True):
        if info.task_id == task_id:
            return info
    return None
