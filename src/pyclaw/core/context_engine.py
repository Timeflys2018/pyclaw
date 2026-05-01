from __future__ import annotations

import asyncio
from typing import Any, Protocol, runtime_checkable

from pyclaw.core.agent.compaction import (
    DEFAULT_COMPACTION_SAFETY_TIMEOUT_S,
    DEFAULT_KEEP_RECENT_TOKENS,
    DEFAULT_THRESHOLD,
    HARDENED_SUMMARIZER_SYSTEM_PROMPT,
    build_summarizer_payload,
    dedupe_duplicate_user_messages,
    estimate_messages_tokens,
    filter_oversized_messages,
    has_real_conversation,
    plan_compaction,
    sanity_check_token_estimate,
    split_into_chunks,
    strip_tool_result_details,
    summarize_in_stages,
)
from pyclaw.models import AssembleResult, CompactResult
from pyclaw.storage.workspace.base import WorkspaceStore


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
        model: str | None = None,
    ) -> CompactResult: ...

    async def after_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None: ...


class _SummarizerCallable(Protocol):
    async def __call__(
        self,
        payload: list[dict[str, Any]],
        *,
        model: str | None = None,
    ) -> str: ...


class DefaultContextEngine:
    def __init__(
        self,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        keep_recent_tokens: int = DEFAULT_KEEP_RECENT_TOKENS,
        summarize: _SummarizerCallable | None = None,
        compaction_timeout_s: float = DEFAULT_COMPACTION_SAFETY_TIMEOUT_S,
        chunk_token_budget: int = 8_000,
        workspace_store: WorkspaceStore | None = None,
        bootstrap_files: list[str] | None = None,
    ) -> None:
        self._threshold = threshold
        self._keep_recent_tokens = keep_recent_tokens
        self._summarize = summarize
        self._compaction_timeout_s = compaction_timeout_s
        self._chunk_token_budget = chunk_token_budget
        self._workspace_store = workspace_store
        self._bootstrap_files: list[str] = bootstrap_files or ["AGENTS.md"]
        self._bootstrap_cache: dict[str, str] = {}

    async def assemble(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
        token_budget: int | None = None,
        prompt: str | None = None,
    ) -> AssembleResult:
        if self._workspace_store is None:
            return AssembleResult(messages=list(messages), system_prompt_addition=None)

        workspace_id = session_id.replace(":", "_")
        if workspace_id in self._bootstrap_cache:
            bootstrap_str = self._bootstrap_cache[workspace_id]
        else:
            from pyclaw.core.context.bootstrap import load_bootstrap_context
            bootstrap_str = await load_bootstrap_context(
                workspace_id, self._workspace_store, self._bootstrap_files
            )
            self._bootstrap_cache[workspace_id] = bootstrap_str

        return AssembleResult(
            messages=list(messages),
            system_prompt_addition=bootstrap_str if bootstrap_str else None,
        )

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
        model: str | None = None,
    ) -> CompactResult:
        if abort_event is not None and abort_event.is_set():
            return CompactResult(
                ok=False,
                compacted=False,
                reason="aborted",
                reason_code="aborted",
            )

        if not has_real_conversation(messages):
            return CompactResult(
                ok=True,
                compacted=False,
                reason="no real conversation",
                reason_code="no_compactable_entries",
            )

        deduped = dedupe_duplicate_user_messages(messages)

        plan = plan_compaction(
            deduped,
            context_window=token_budget,
            threshold=1.0 if force else self._threshold,
            keep_recent_tokens=self._keep_recent_tokens,
        )
        if not plan.should_compact or plan.cut_index is None:
            return CompactResult(
                ok=True,
                compacted=False,
                reason=plan.reason,
                reason_code=(
                    "below_threshold"
                    if plan.reason == "within-budget"
                    else "no_compactable_entries"
                ),
                tokens_before=plan.estimated_tokens,
            )

        to_summarize_raw = deduped[: plan.cut_index]
        to_summarize = strip_tool_result_details(to_summarize_raw)
        to_summarize = filter_oversized_messages(
            to_summarize, context_window=token_budget
        )

        if self._summarize is None:
            summary = _fallback_summary(to_summarize)
        else:
            try:
                summary = await self._run_summarizer_guarded(
                    to_summarize,
                    abort_event=abort_event,
                    model=model,
                )
            except _CompactionTimeout:
                return CompactResult(
                    ok=False,
                    compacted=False,
                    reason="summarizer timeout",
                    reason_code="timeout",
                    tokens_before=plan.estimated_tokens,
                )
            except _CompactionAborted:
                return CompactResult(
                    ok=False,
                    compacted=False,
                    reason="aborted",
                    reason_code="aborted",
                    tokens_before=plan.estimated_tokens,
                )
            except Exception as exc:
                return CompactResult(
                    ok=False,
                    compacted=False,
                    reason=f"summary failed: {exc}",
                    reason_code="summary_failed",
                    tokens_before=plan.estimated_tokens,
                )

        tokens_after_raw = plan.kept_tokens + estimate_messages_tokens(
            [{"role": "assistant", "content": summary}]
        )
        tokens_after = sanity_check_token_estimate(plan.estimated_tokens, tokens_after_raw)

        return CompactResult(
            ok=True,
            compacted=True,
            summary=summary,
            first_kept_entry_id=None,
            tokens_before=plan.estimated_tokens,
            tokens_after=tokens_after,
            reason=f"compacted-at-{plan.cut_index}",
            reason_code="compacted",
        )

    async def _run_summarizer_guarded(
        self,
        messages: list[dict[str, Any]],
        *,
        abort_event: asyncio.Event | None,
        model: str | None,
    ) -> str:
        from pyclaw.core.agent.runtime_util import (
            AgentAbortedError,
            AgentTimeoutError,
            with_safety_timeout,
        )

        summarize_fn = self._summarize

        async def _one_stage(payload: list[dict[str, Any]]) -> str:
            assert summarize_fn is not None
            effective_payload = build_summarizer_payload(payload)
            effective_payload[0] = {
                "role": "system",
                "content": HARDENED_SUMMARIZER_SYSTEM_PROMPT,
            }
            try:
                return await summarize_fn(effective_payload, model=model)
            except TypeError:
                return await summarize_fn(effective_payload)  # type: ignore[call-arg]

        chunks = split_into_chunks(
            messages, chunk_token_budget=self._chunk_token_budget
        )

        async def _run() -> str:
            if len(chunks) <= 1:
                return await _one_stage(messages)
            return await summarize_in_stages(
                messages,
                summarizer=_one_stage,
                chunk_token_budget=self._chunk_token_budget,
            )

        try:
            return await with_safety_timeout(
                _run,
                timeout_s=self._compaction_timeout_s,
                abort_event=abort_event,
                kind="compaction",
            )
        except AgentTimeoutError as te:
            raise _CompactionTimeout() from te
        except AgentAbortedError as ae:
            raise _CompactionAborted() from ae

    async def after_turn(self, session_id: str, messages: list[dict[str, Any]]) -> None:
        return None


class _CompactionTimeout(Exception):
    pass


class _CompactionAborted(Exception):
    pass


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
