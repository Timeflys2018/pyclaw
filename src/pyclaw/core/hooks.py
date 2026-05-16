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
"""A hook's decision for one tool call.

- ``approve`` — execute the tool
- ``deny`` — skip the tool, append a denial error to the conversation
- ``wait`` — reserved for future async approval flows (not currently emitted by
  any built-in hook; runner treats unknown decisions as ``deny`` for safety)
"""


PermissionTier = Literal["read-only", "approval", "yolo"]
"""User-controlled autonomy tier governing tool execution.

Defined by spec ``tool-approval-tiers``:

- ``read-only`` — write-class tools are auto-denied without prompt; read-class
  tools and ``memorize`` execute freely
- ``approval`` — tools listed in the channel's ``tools_requiring_approval``
  config trigger the approval flow; others auto-approve
- ``yolo`` — all tools auto-approve, gate effectively disabled
"""


@runtime_checkable
class SkillProvider(Protocol):
    def resolve_skills_prompt(self, workspace_path: str) -> str | None: ...


@runtime_checkable
class ToolApprovalHook(Protocol):
    """Per-channel approval gate for tool execution.

    The runner invokes ``before_tool_execution`` (when a hook is registered on
    ``AgentRunnerDeps``) **only for the subset of approval-tier calls that are
    actually gated**, as determined per-call by the runner using
    :meth:`should_gate` plus ``tier_source``. Each tool call gets one decision;
    the runner skips denied calls and appends a denial error message in their
    place.

    The ``tier`` argument carries the active :data:`PermissionTier` for the
    current turn (always ``"approval"`` when this method is called).

    Sprint 2.0.1 hotfix amendment (event-flow vs decision-flow lockstep):
    Calls with ``call_tier == "approval"`` but NOT actually gated (i.e. neither
    ``forced_tier == "approval"`` nor :meth:`should_gate` returns True) are
    auto-approved by the runner directly, with NO ``ToolApprovalRequest`` event
    emission and NO call to this hook. This prevents the "phantom modal" bug
    where non-gated calls trigger a UI prompt the user cannot influence.
    """

    def should_gate(self, tool_name: str) -> bool:
        """Synchronous predicate: should this tool name require user approval?

        Called by the runner during per-call tier evaluation, BEFORE emitting
        any user-visible :class:`ToolApprovalRequest` event. Only when this
        returns ``True`` (or the call is forced-by-server-config) will the
        runner emit the event and call :meth:`before_tool_execution` for this
        call.

        MUST be synchronous and read-only on settings (no I/O, no awaits).

        MUST NOT be called by the runner when
        ``tier_source == "forced-by-server-config"``: forced-tier calls are
        unconditionally gated per Sprint 2 spec invariant, bypassing the
        per-channel ``tools_requiring_approval`` allow-list.
        """
        ...

    async def before_tool_execution(
        self,
        tool_calls: list[dict[str, Any]],
        session_id: str,
        tier: PermissionTier,
    ) -> list[ApprovalDecision]: ...


@runtime_checkable
class AgentHook(Protocol):
    """Hook protocol for the agent loop.

    Required: before_prompt_build, after_response, before_compaction,
    after_compaction.

    Optional (discovered via getattr by HookRegistry): on_run_start,
    on_run_end.
    """

    async def before_prompt_build(
        self, context: PromptBuildContext
    ) -> PromptBuildResult | None: ...
    async def after_response(self, observation: ResponseObservation) -> None: ...
    async def before_compaction(self, context: CompactionContext) -> None: ...
    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None: ...


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

    async def notify_run_start(self, session_id: str, control: RunControl) -> None:
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
