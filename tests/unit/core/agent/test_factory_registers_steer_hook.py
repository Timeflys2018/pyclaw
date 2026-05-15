from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from pyclaw.core.agent.factory import create_agent_runner_deps
from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook
from pyclaw.core.agent.hooks.steer_hook import SteerHook
from pyclaw.core.agent.hooks.working_memory_hook import WorkingMemoryHook
from pyclaw.infra.settings import Settings
from pyclaw.storage.session.base import InMemorySessionStore


async def test_steer_hook_registered_without_memory_store() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    deps = await create_agent_runner_deps(settings, store)

    hook_types = [type(h) for h in deps.hooks.hooks()]
    assert SteerHook in hook_types, (
        "SteerHook must register unconditionally, even when memory/redis are disabled"
    )


async def test_steer_hook_registered_last_in_order() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    mem_store = AsyncMock()
    redis_client = MagicMock()

    deps = await create_agent_runner_deps(
        settings, store, memory_store=mem_store, redis_client=redis_client
    )

    hooks = deps.hooks.hooks()
    hook_types = [type(h) for h in hooks]

    assert WorkingMemoryHook in hook_types
    assert MemoryNudgeHook in hook_types
    assert SteerHook in hook_types

    wm_idx = hook_types.index(WorkingMemoryHook)
    nudge_idx = hook_types.index(MemoryNudgeHook)
    steer_idx = hook_types.index(SteerHook)

    assert wm_idx < steer_idx, "SteerHook must register AFTER WorkingMemoryHook"
    assert nudge_idx < steer_idx, "SteerHook must register AFTER MemoryNudgeHook"


async def test_steer_hook_registered_exactly_once() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    mem_store = AsyncMock()
    redis_client = MagicMock()

    deps = await create_agent_runner_deps(
        settings, store, memory_store=mem_store, redis_client=redis_client
    )

    steer_hook_count = sum(1 for h in deps.hooks.hooks() if isinstance(h, SteerHook))
    assert steer_hook_count == 1
