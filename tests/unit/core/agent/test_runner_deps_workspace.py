from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyclaw.core.agent.factory import create_agent_runner_deps
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.infra.settings import Settings
from pyclaw.storage.session.base import InMemorySessionStore
from pyclaw.storage.workspace.file import FileWorkspaceStore


def test_agent_runner_deps_workspace_store_default_none() -> None:
    from pyclaw.core.agent.llm import LLMClient
    from pyclaw.core.agent.tools.registry import ToolRegistry
    deps = AgentRunnerDeps(
        llm=LLMClient(default_model="fake"),
        tools=ToolRegistry(),
    )
    assert deps.workspace_store is None


async def test_factory_passes_workspace_store_to_deps(tmp_path: Path) -> None:
    settings = Settings()
    store_ws = FileWorkspaceStore(base_dir=tmp_path)
    deps = await create_agent_runner_deps(settings, InMemorySessionStore(), workspace_store=store_ws)
    assert deps.workspace_store is store_ws


async def test_factory_passes_workspace_store_to_engine(tmp_path: Path) -> None:
    settings = Settings()
    store_ws = FileWorkspaceStore(base_dir=tmp_path)
    deps = await create_agent_runner_deps(settings, InMemorySessionStore(), workspace_store=store_ws)
    assert isinstance(deps.context_engine, DefaultContextEngine)
    assert deps.context_engine._workspace_store is store_ws


async def test_factory_without_workspace_store_engine_has_none() -> None:
    settings = Settings()
    deps = await create_agent_runner_deps(settings, InMemorySessionStore())
    assert deps.workspace_store is None
    assert isinstance(deps.context_engine, DefaultContextEngine)
    assert deps.context_engine._workspace_store is None
