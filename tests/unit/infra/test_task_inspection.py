"""Tests for task_inspection pure ops (Phase A3-tasks)."""

from __future__ import annotations

import asyncio

import pytest

from pyclaw.infra.task_inspection import describe, list_all, list_for_owner
from pyclaw.infra.task_manager import TaskManager


@pytest.fixture
async def tm():
    tm = TaskManager()
    yield tm
    await tm.shutdown(grace_s=1.0)


class TestListForOwner:
    @pytest.mark.asyncio
    async def test_filters_by_owner(self, tm: TaskManager) -> None:
        async def hang():
            await asyncio.sleep(10)

        tm.spawn("a", hang(), owner="web:user_a")
        tm.spawn("b", hang(), owner="web:user_b")
        tm.spawn("c", hang(), owner="web:user_a")
        tm.spawn("sys", hang())

        results = list_for_owner(tm, owner="web:user_a")
        assert len(results) == 2
        assert {i.name for i in results} == {"a", "c"}

    @pytest.mark.asyncio
    async def test_tm_none_raises(self) -> None:
        with pytest.raises(ValueError, match="task_manager is None"):
            list_for_owner(None, owner="any")

    @pytest.mark.asyncio
    async def test_empty_when_no_match(self, tm: TaskManager) -> None:
        async def hang():
            await asyncio.sleep(10)

        tm.spawn("a", hang(), owner="web:user_a")
        assert list_for_owner(tm, owner="nobody") == []


class TestListAll:
    @pytest.mark.asyncio
    async def test_includes_system_tasks(self, tm: TaskManager) -> None:
        async def hang():
            await asyncio.sleep(10)

        tm.spawn("user", hang(), owner="web:user_a")
        tm.spawn("sys", hang())

        results = list_all(tm)
        assert len(results) == 2
        assert {i.name for i in results} == {"user", "sys"}

    @pytest.mark.asyncio
    async def test_tm_none_raises(self) -> None:
        with pytest.raises(ValueError, match="task_manager is None"):
            list_all(None)


class TestDescribe:
    @pytest.mark.asyncio
    async def test_returns_matching_task(self, tm: TaskManager) -> None:
        async def hang():
            await asyncio.sleep(10)

        tid = tm.spawn("x", hang(), owner="web:user_a")
        info = describe(tm, tid)
        assert info is not None
        assert info.task_id == tid
        assert info.name == "x"
        assert info.owner == "web:user_a"

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown(self, tm: TaskManager) -> None:
        assert describe(tm, "nonexistent") is None

    @pytest.mark.asyncio
    async def test_tm_none_raises(self) -> None:
        with pytest.raises(ValueError, match="task_manager is None"):
            describe(None, "any")
