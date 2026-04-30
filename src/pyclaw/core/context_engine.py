from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from pyclaw.core.agent.compaction import (
    DEFAULT_KEEP_RECENT_TOKENS,
    DEFAULT_THRESHOLD,
    build_summarizer_payload,
    plan_compaction,
)
from pyclaw.models import AssembleResult, CompactResult


@runtime_checkable
class ContextEngine(Protocol):
    async def assemble(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
        token_budget: int | None = None,
        prompt: str | None = None,
    ) -> AssembleResult: ...

    async def ingest(self, session_id: str, message: dict[str, Any]) -> None: ...

    async def compact(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
        token_budget: int,
        force: bool = False,
        abort_event: asyncio.Event | None = None,
    ) -> CompactResult: ...

    async def after_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None: ...


class DefaultContextEngine:
    def __init__(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
        summarize: _SummarizerCallable | None = None,
    ) -> None:
        self._threshold = threshold
        self._keep_recent_tokens = keep_recent_tokens
        self._summarize = summarize

    async def assemble(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
        token_budget: int | None = None,
        prompt: str | None = None,
    ) -> AssembleResult:
        return AssembleResult(messages=list(messages), system_prompt_addition=None)

    async def ingest(self, session_id: str, message: dict[str, Any]) -> None:
        return None

    async def compact(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
        token_budget: int,
        force: bool = False,
        abort_event: asyncio.Event | None = None,
    ) -> CompactResult:
        if abort_event is not None and abort_event.is_set():
            return CompactResult(
                ok=False,
                compacted=False,
                reason="aborted",
                reason_code="aborted",
            )

        plan = plan_compaction(
            messages,
            context_window=token_budget,
            threshold=1.0 if force else self._threshold,
            keep_recent_tokens=self._keep_recent_tokens,
        )
        if not plan.should_compact or plan.cut_index is None:
            return CompactResult(
                ok=True,
                compacted=False,
                reason=plan.reason,
                reason_code="below_threshold"
                if plan.reason == "within-budget"
                else "no_compactable_entries",
                tokens_before=plan.estimated_tokens,
            )

        to_summarize = messages[: plan.cut_index]
        _kept = messages[plan.cut_index :]

        if self._summarize is None:
            summary = _fallback_summary(to_summarize)
        else:
            summarize_coro = self._summarize(build_summarizer_payload(to_summarize))
            if abort_event is None:
                summary = await summarize_coro
            else:
                from pyclaw.core.agent.runtime_util import (
                    AgentAbortedError,
                    run_with_timeout,
                )

                try:
                    summary = await run_with_timeout(
                        summarize_coro,
                        timeout_s=0.0,
                        abort_event=abort_event,
                        kind="compaction",
                    )
                except AgentAbortedError:
                    return CompactResult(
                        ok=False,
                        compacted=False,
                        reason="aborted",
                        reason_code="aborted",
                        tokens_before=plan.estimated_tokens,
                    )

        return CompactResult(
            ok=True,
            compacted=True,
            summary=summary,
            first_kept_entry_id=None,
            tokens_before=plan.estimated_tokens,
            tokens_after=plan.kept_tokens,
            reason=f"compacted-at-{plan.cut_index}",
            reason_code="compacted",
        )

    async def after_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        return None


class _SummarizerCallable(Protocol):
    async def __call__(self, payload: list[dict[str, Any]]) -> str: ...


def _fallback_summary(messages: list[dict[str, Any]]) -> str:
    lines = [f"[summary of {len(messages)} prior messages]"]
    for m in messages[:3]:
        role = m.get("role", "user")
        content = m.get("content", "")
        if isinstance(content, str):
            snippet = content[:120].replace("\n", " ")
            lines.append(f"- {role}: {snippet}")
    if len(messages) > 3:
        lines.append(f"- ... ({len(messages) - 3} more)")
    return "\n".join(lines)
