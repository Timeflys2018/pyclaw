from __future__ import annotations

import os
from pathlib import Path

import pytest

from pyclaw.core.agent.llm import LLMClient
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.core.agent.tools.builtin import register_builtin_tools
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.agent.tools.workspace import WorkspaceResolver
from pyclaw.models import WorkspaceConfig
from pyclaw.storage.session.base import InMemorySessionStore


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "e2e: end-to-end tests requiring real LLM API (PYCLAW_LLM_API_KEY env var)",
    )


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if not os.environ.get("PYCLAW_LLM_API_KEY"):
        skip = pytest.mark.skip(reason="PYCLAW_LLM_API_KEY not set")
        for item in items:
            if item.get_closest_marker("e2e"):
                item.add_marker(skip)


@pytest.fixture(scope="session")
def llm_client() -> LLMClient:
    return LLMClient(
        default_model=os.environ.get(
            "PYCLAW_LLM_MODEL", "anthropic/ppio/pa/claude-sonnet-4-6"
        ),
        api_key=os.environ.get("PYCLAW_LLM_API_KEY"),
        api_base=os.environ.get("PYCLAW_LLM_API_BASE"),
    )


@pytest.fixture
def session_store() -> InMemorySessionStore:
    return InMemorySessionStore()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def tool_registry(workspace: Path) -> ToolRegistry:
    registry = ToolRegistry()
    config = WorkspaceConfig(workspaces={"default": str(workspace)})
    resolver = WorkspaceResolver(config)
    register_builtin_tools(registry, resolver)
    return registry


@pytest.fixture
def agent_deps(
    llm_client: LLMClient,
    tool_registry: ToolRegistry,
    session_store: InMemorySessionStore,
) -> AgentRunnerDeps:
    from pyclaw.core.hooks import HookRegistry
    from pyclaw.models import AgentRunConfig

    return AgentRunnerDeps(
        llm=llm_client,
        tools=tool_registry,
        session_store=session_store,
        hooks=HookRegistry(),
        config=AgentRunConfig(max_iterations=10),
    )
