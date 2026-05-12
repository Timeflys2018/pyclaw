from __future__ import annotations

from pathlib import Path

from pyclaw.core.agent.llm import LLMClient, LLMError, resolve_provider_for_model
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.core.agent.tools.builtin import register_builtin_tools
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.agent.tools.workspace import WorkspaceResolver
from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.core.hooks import HookRegistry
from pyclaw.infra.settings import Settings
from pyclaw.infra.task_manager import TaskManager
from pyclaw.models import AgentRunConfig, WorkspaceConfig
from pyclaw.storage.memory.base import MemoryStore
from pyclaw.storage.protocols import SessionStore
from pyclaw.storage.workspace.base import WorkspaceStore


async def create_agent_runner_deps(
    settings: Settings,
    session_store: SessionStore,
    workspace_store: WorkspaceStore | None = None,
    task_manager: TaskManager | None = None,
    memory_store: MemoryStore | None = None,
    redis_client=None,
    lock_manager=None,
) -> AgentRunnerDeps:
    llm = LLMClient(
        default_model=settings.agent.default_model,
        providers=settings.agent.providers,
        default_provider=settings.agent.default_provider,
        unknown_prefix_policy=settings.agent.unknown_prefix_policy,
    )

    if settings.agent.providers:
        try:
            resolve_provider_for_model(
                settings.agent.default_model,
                settings.agent.providers,
                default_provider=settings.agent.default_provider,
                unknown_prefix_policy=settings.agent.unknown_prefix_policy,
            )
        except LLMError as exc:
            raise RuntimeError(
                f"agent.default_model='{settings.agent.default_model}' cannot be routed "
                f"to any configured provider. Fix configs/pyclaw.json before starting. {exc}"
            ) from exc

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
            "compaction": settings.agent.compaction.model_dump(by_alias=True),
            "tools": settings.agent.tools.model_dump(),
            "prompt_budget": settings.agent.prompt_budget.model_dump(),
        }
    )

    config.prompt_budget.validate_against_context_window(config.context_window)

    bootstrap_files = list(getattr(getattr(settings, "workspaces", None), "bootstrap_files", None) or ["AGENTS.md"])
    engine = DefaultContextEngine(
        workspace_store=workspace_store,
        bootstrap_files=bootstrap_files,
        memory_store=memory_store,
        memory_settings=settings.memory,
    )

    from pyclaw.skills.provider import DefaultSkillProvider

    skill_provider = DefaultSkillProvider(settings=settings.skills)

    if settings.skills.progressive_disclosure:
        from pyclaw.core.agent.tools.skill_view import SkillViewTool

        tools.register(SkillViewTool(skill_provider))

    hooks = HookRegistry()

    if memory_store is not None:
        from pyclaw.core.agent.tools.forget import ForgetTool
        from pyclaw.core.agent.tools.memorize import MemorizeTool

        tools.register(MemorizeTool(memory_store, session_store))
        tools.register(ForgetTool(memory_store, session_store))

        if redis_client is not None:
            from pyclaw.core.agent.hooks.working_memory_hook import WorkingMemoryHook
            from pyclaw.core.agent.tools.update_working_memory import (
                UpdateWorkingMemoryTool,
            )

            tools.register(UpdateWorkingMemoryTool(redis_client))
            hooks.register(WorkingMemoryHook(redis_client))

        from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook

        nudge_hook = MemoryNudgeHook(interval=10)
        hooks.register(nudge_hook)

        if redis_client is not None and settings.evolution.enabled:
            from pyclaw.core.agent.hooks.sop_tracker_hook import SopCandidateTracker

            hooks.register(SopCandidateTracker(
                redis_client,
                settings.evolution,
                task_manager=task_manager,
                memory_store=memory_store,
                session_store=session_store,
                llm_client=llm,
                nudge_hook=nudge_hook,
            ))

    return AgentRunnerDeps(
        llm=llm,
        tools=tools,
        context_engine=engine,
        hooks=hooks,
        session_store=session_store,
        config=config,
        workspace_store=workspace_store,
        skill_provider=skill_provider,
        task_manager=task_manager,
        lock_manager=lock_manager,
    )
