from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from pyclaw.models import CompactResult


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


@runtime_checkable
class AgentHook(Protocol):
    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None: ...
    async def after_response(self, observation: ResponseObservation) -> None: ...


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: list[AgentHook] = []

    def register(self, hook: AgentHook) -> None:
        self._hooks.append(hook)

    def hooks(self) -> list[AgentHook]:
        return list(self._hooks)

    async def collect_prompt_additions(self, context: PromptBuildContext) -> PromptBuildResult:
        prepends: list[str] = []
        appends: list[str] = []
        for hook in self._hooks:
            result = await hook.before_prompt_build(context)
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
        for hook in self._hooks:
            await hook.after_response(observation)

    async def notify_before_compaction(self, context: CompactionContext) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            before = getattr(hook, "before_compaction", None)
            if before is None:
                continue
            try:
                await before(context)
            except Exception:
                logger.exception("before_compaction hook failed: %r", hook)

    async def notify_after_compaction(
        self, context: CompactionContext, result: CompactResult
    ) -> None:
        import logging

        logger = logging.getLogger(__name__)
        for hook in self._hooks:
            after = getattr(hook, "after_compaction", None)
            if after is None:
                continue
            try:
                await after(context, result)
            except Exception:
                logger.exception("after_compaction hook failed: %r", hook)
