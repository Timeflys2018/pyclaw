from __future__ import annotations

from pyclaw.core.hooks import (
    CompactionContext,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)
from pyclaw.models import CompactResult


_NUDGE_TEXT = (
    "<nudge>Consider using `memorize` to save important facts or workflows "
    "for future sessions.</nudge>"
)


class MemoryNudgeHook:
    """AgentHook that periodically reminds the agent to memorize."""

    def __init__(self, interval: int = 10) -> None:
        if interval <= 0:
            raise ValueError("interval must be positive")
        self._interval = interval
        self._counts: dict[str, int] = {}

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        session_id = context.session_id
        self._counts[session_id] = self._counts.get(session_id, 0) + 1
        count = self._counts[session_id]
        if count > 0 and count % self._interval == 0:
            return PromptBuildResult(append=_NUDGE_TEXT)
        return None

    async def after_response(self, observation: ResponseObservation) -> None:
        for call in observation.tool_calls:
            fn = (call or {}).get("function") or {}
            name = fn.get("name", "") if isinstance(fn, dict) else ""
            if name == "memorize":
                self._counts[observation.session_id] = 0
                return
        return None

    async def before_compaction(self, context: CompactionContext) -> None:
        return None

    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None:
        return None
