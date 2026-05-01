from __future__ import annotations

from pathlib import Path

from pyclaw.core.agent.llm import LLMClient
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.core.agent.tools.builtin import register_builtin_tools
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.agent.tools.workspace import WorkspaceResolver
from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.core.hooks import HookRegistry
from pyclaw.infra.settings import Settings
from pyclaw.models import AgentRunConfig, WorkspaceConfig
from pyclaw.storage.protocols import SessionStore
from pyclaw.storage.workspace.base import WorkspaceStore


def create_agent_runner_deps(
    settings: Settings,
    session_store: SessionStore,
    workspace_store: WorkspaceStore | None = None,
) -> AgentRunnerDeps:
    api_key: str | None = None
    base_url: str | None = None
    default_model = settings.agent.default_model

    for _prefix, provider in settings.agent.providers.items():
        if provider.api_key:
            api_key = provider.api_key
            base_url = provider.base_url
            break

    llm = LLMClient(default_model=default_model, api_key=api_key, api_base=base_url)

    workspace_default = getattr(
        getattr(settings, "workspaces", None), "default", None
    ) or str(Path.home() / "pyclaw-workspace")
    workspace_config = WorkspaceConfig(workspaces={"default": workspace_default})
    resolver = WorkspaceResolver(workspace_config)

    tools = ToolRegistry()
    register_builtin_tools(tools, resolver)

    config = AgentRunConfig.model_validate(
        {
            "max_iterations": settings.agent.max_iterations,
            "context_window": settings.agent.max_context_tokens,
            "timeouts": settings.agent.timeouts.model_dump(),
            "retry": settings.agent.retry.model_dump(),
            "compaction": settings.agent.compaction.model_dump(),
            "tools": settings.agent.tools.model_dump(),
        }
    )

    bootstrap_files = list(getattr(getattr(settings, "workspaces", None), "bootstrap_files", None) or ["AGENTS.md"])
    engine = DefaultContextEngine(
        workspace_store=workspace_store,
        bootstrap_files=bootstrap_files,
    )

    return AgentRunnerDeps(
        llm=llm,
        tools=tools,
        context_engine=engine,
        hooks=HookRegistry(),
        session_store=session_store,
        config=config,
        workspace_store=workspace_store,
        skill_settings=settings.skills,
    )
