from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.agent.factory import create_agent_runner_deps
from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook
from pyclaw.core.agent.hooks.working_memory_hook import WorkingMemoryHook
from pyclaw.infra.settings import Settings
from pyclaw.storage.session.base import InMemorySessionStore


async def test_memory_tools_not_registered_without_memory_store() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    deps = await create_agent_runner_deps(settings, store)

    assert "memorize" not in deps.tools
    assert "update_working_memory" not in deps.tools

    hook_types = {type(h) for h in deps.hooks.hooks()}
    assert WorkingMemoryHook not in hook_types
    assert MemoryNudgeHook not in hook_types


async def test_memorize_registered_with_memory_store_only() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    mem_store = AsyncMock()

    deps = await create_agent_runner_deps(
        settings, store, memory_store=mem_store, redis_client=None
    )

    assert "memorize" in deps.tools
    assert "update_working_memory" not in deps.tools

    hook_types = {type(h) for h in deps.hooks.hooks()}
    assert WorkingMemoryHook not in hook_types
    assert MemoryNudgeHook in hook_types


async def test_all_memory_components_registered_with_both_clients() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    mem_store = AsyncMock()
    redis_client = MagicMock()

    deps = await create_agent_runner_deps(
        settings, store, memory_store=mem_store, redis_client=redis_client
    )

    assert "memorize" in deps.tools
    assert "update_working_memory" in deps.tools

    hooks = deps.hooks.hooks()
    hook_types = [type(h) for h in hooks]
    assert WorkingMemoryHook in hook_types
    assert MemoryNudgeHook in hook_types

    wm_idx = hook_types.index(WorkingMemoryHook)
    nudge_idx = hook_types.index(MemoryNudgeHook)
    assert wm_idx < nudge_idx


async def test_memory_store_passed_to_context_engine() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    mem_store = AsyncMock()

    deps = await create_agent_runner_deps(
        settings, store, memory_store=mem_store
    )

    from pyclaw.core.context_engine import DefaultContextEngine

    assert isinstance(deps.context_engine, DefaultContextEngine)
    assert deps.context_engine._memory_store is mem_store
