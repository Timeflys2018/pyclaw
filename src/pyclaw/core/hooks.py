from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable

from pyclaw.models import CompactResult

if TYPE_CHECKING:
    from pyclaw.core.agent.run_control import RunControl


@dataclass
class PromptBuildContext:
    session_id: str
    workspace_id: str
    agent_id: str
    available_tools: list[str] = field(default_factory=list)
    prompt: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass
class PromptBuildResult:
    prepend: str | None = None
    append: str | None = None


@dataclass
class ResponseObservation:
    session_id: str
    assistant_text: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class CompactionContext:
    session_id: str
    workspace_id: str
    agent_id: str
    tokens_before: int = 0
    message_count: int = 0
    extras: dict[str, Any] = field(default_factory=dict)


ApprovalDecision = Literal["approve", "deny", "wait"]


@runtime_checkable
class SkillProvider(Protocol):
    def resolve_skills_prompt(self, workspace_path: str) -> str | None: ...


@runtime_checkable
class ToolApprovalHook(Protocol):
    async def before_tool_execution(
        self, tool_calls: list[dict], session_id: str,
    ) -> list[ApprovalDecision]: ...


@runtime_checkable
class AgentHook(Protocol):
    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None: ...
    async def after_response(self, observation: ResponseObservation) -> None: ...
    async def before_compaction(self, context: CompactionContext) -> None: ...
    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None: ...
    async def on_run_start(self, session_id: str, control: "RunControl") -> None: ...
    async def on_run_end(self, session_id: str, terminated_by: str) -> None: ...


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: list[AgentHook] = []

    def register(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    def hooks(self) -> list[AgentHook]:
        return list(self._hooks)

    async def collect_prompt_additions(self, context: PromptBuildContext) -> PromptBuildResult:
        import logging

        logger = logging.getLogger(__name__)
        prepends: list[str] = []
        appends: list[str] = []
        for hook in self._hooks:
            try:
                result = await hook.before_prompt_build(context)
            except Exception:
                logger.exception("before_prompt_build hook failed: %r", hook)
                continue
            if result is None:
                continue
            if result.prepend:
                prepends.append(result.prepend)
            if result.append:
                appends.append(result.append)
        return PromptBuildResult(
            prepend="\n\n".join(prepends) if prepends else None,
            append="\n\n".join(appends) if appends else None,
        )

    async def notify_response(self, observation: ResponseObservation) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            try:
                await hook.after_response(observation)
            except Exception:
                logger.exception("after_response hook failed: %r", hook)

    async def notify_before_compaction(self, context: CompactionContext) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            try:
                await hook.before_compaction(context)
            except Exception:
                logger.exception("before_compaction hook failed: %r", hook)

    async def notify_after_compaction(
        self, context: CompactionContext, result: CompactResult
    ) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            try:
                await hook.after_compaction(context, result)
            except Exception:
                logger.exception("after_compaction hook failed: %r", hook)

    async def notify_run_start(self, session_id: str, control: "RunControl") -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            method = getattr(hook, "on_run_start", None)
            if method is None:
                continue
            try:
                await method(session_id, control)
            except Exception:
                logger.exception("on_run_start hook failed: %r", hook)

    async def notify_run_end(self, session_id: str, terminated_by: str) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            method = getattr(hook, "on_run_end", None)
            if method is None:
                continue
            try:
                await method(session_id, terminated_by)
            except Exception:
                logger.exception("on_run_end hook failed: %r", hook)
