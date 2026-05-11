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

ARCHIVE_CACHE_MAX_ENTRIES = 200


def _derive_workspace_id(session_id: str) -> str:
    idx = session_id.find(":s:")
    session_key = session_id[:idx] if idx != -1 else session_id
    return session_key.replace(":", "_")


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
        memory_store: Any = None,
        memory_settings: Any = None,
    ) -> None:
        self._threshold = threshold
        self._keep_recent_tokens = keep_recent_tokens
        self._summarize = summarize
        self._compaction_timeout_s = compaction_timeout_s
        self._chunk_token_budget = chunk_token_budget
        self._workspace_store = workspace_store
        self._bootstrap_files: list[str] = bootstrap_files if bootstrap_files is not None else ["AGENTS.md"]
        self._bootstrap_cache: dict[str, str] = {}
        self._memory_store = memory_store
        self._l1_cache: dict[str, list[Any]] = {}
        self._archive_cache: dict[str, tuple[str, list[Any]]] = {}
        if memory_settings is None:
            from pyclaw.infra.settings import MemorySettings
            memory_settings = MemorySettings()
        self._memory_settings = memory_settings

    def _derive_session_key(self, session_id: str) -> str:
        idx = session_id.find(":s:")
        return session_id[:idx] if idx != -1 else session_id

    async def get_bootstrap(self, session_id: str) -> str | None:
        if self._workspace_store is None:
            return None
        workspace_id = _derive_workspace_id(session_id)
        if workspace_id in self._bootstrap_cache:
            cached = self._bootstrap_cache[workspace_id]
            return cached if cached else None
        from pyclaw.core.context.bootstrap import load_bootstrap_context
        bootstrap_str = await load_bootstrap_context(
            workspace_id, self._workspace_store, self._bootstrap_files
        )
        self._bootstrap_cache[workspace_id] = bootstrap_str
        return bootstrap_str if bootstrap_str else None

    async def get_l1_snapshot(self, session_id: str) -> list[Any]:
        if self._memory_store is None:
            return []
        if session_id in self._l1_cache:
            return self._l1_cache[session_id]
        session_key = self._derive_session_key(session_id)
        try:
            entries = await self._memory_store.index_get(session_key)
        except Exception:
            import logging
            logging.getLogger(__name__).warning(
                "L1 snapshot load failed for session %s", session_id, exc_info=True
            )
            entries = []
        self._l1_cache[session_id] = entries
        return entries

    async def assemble(
        self,
        *,
        session_id: str,
        messages: list[dict[str, Any]],
        token_budget: int | None = None,
        prompt: str | None = None,
    ) -> AssembleResult:
        l2l3_results: list[Any] = []
        archive_results: list[Any] = []
        if self._memory_store is not None and prompt:
            session_key = self._derive_session_key(session_id)
            cfg = self._memory_settings
            try:
                l2l3_results = await self._memory_store.search(
                    session_key, prompt,
                    layers=["L2", "L3"],
                    per_layer_limits={"L2": cfg.search_l2_quota, "L3": cfg.search_l3_quota},
                )
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "memory_store.search (L2/L3) failed for session %s", session_id, exc_info=True,
                )
            if cfg.archive_enabled:
                cached = self._archive_cache.get(session_id)
                if cached is not None and cached[0] == prompt:
                    archive_results = list(cached[1])
                else:
                    try:
                        archive_results = await self._memory_store.search_archives(
                            session_key, prompt,
                            limit=cfg.archive_max_results,
                            min_similarity=cfg.archive_min_similarity,
                        )
                        if len(self._archive_cache) >= ARCHIVE_CACHE_MAX_ENTRIES:
                            oldest_session_id = next(iter(self._archive_cache))
                            del self._archive_cache[oldest_session_id]
                        self._archive_cache[session_id] = (prompt, archive_results)
                    except Exception:
                        import logging
                        logging.getLogger(__name__).warning(
                            "memory_store.search_archives (L4) failed for session %s", session_id, exc_info=True,
                        )

        memory_context = self._format_memory_context(l2l3_results, archive_results)
        return AssembleResult(
            messages=list(messages),
            system_prompt_addition=memory_context,
        )

    @staticmethod
    def _format_memory_context(
        l2l3_results: list[Any],
        archive_results: list[Any],
    ) -> str | None:
        l2_entries = [r for r in l2l3_results if getattr(r, "layer", "") == "L2"]
        l3_entries = [r for r in l2l3_results if getattr(r, "layer", "") == "L3"]
        if not l2_entries and not l3_entries and not archive_results:
            return None
        lines = ["<memory_context>"]
        if l2_entries:
            lines.append("<facts>")
            for entry in l2_entries:
                lines.append(f"- [{getattr(entry, 'type', 'general')}] {getattr(entry, 'content', '')}")
            lines.append("</facts>")
        if l3_entries:
            lines.append("<procedures>")
            for entry in l3_entries:
                entry_id = getattr(entry, 'id', '')[:8]
                lines.append(
                    f"- [{getattr(entry, 'type', 'general')}|{entry_id}] "
                    f"{getattr(entry, 'content', '')}"
                )
            lines.append("</procedures>")
        if archive_results:
            lines.append("<archives>")
            for entry in archive_results:
                sid_short = getattr(entry, "session_id", "").split(":")[-1][:12]
                similarity = getattr(entry, "similarity", None)
                sim_str = f"{similarity:.2f}" if isinstance(similarity, (int, float)) and not isinstance(similarity, bool) else "?"
                summary = getattr(entry, "summary", "")
                lines.append(f"- [session={sid_short}|sim={sim_str}] {summary}")
            lines.append("</archives>")
        lines.append("</memory_context>")
        return "\n".join(lines)

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
        self._archive_cache.pop(session_id, None)


class _CompactionTimeout(Exception):
    pass


class _CompactionAborted(Exception):
    pass


def _fallback_summary(messages: list[dict[str, Any]]) -> str:
    from pyclaw.models.utils import extract_text_from_content

    lines = [f"[summary of {len(messages)} prior messages]"]
    for m in messages[:3]:
        role = m.get("role", "user")
        text = extract_text_from_content(m.get("content"))
        if text:
            snippet = text[:120].replace("\n", " ")
            lines.append(f"- {role}: {snippet}")
    if len(messages) > 3:
        lines.append(f"- ... ({len(messages) - 3} more)")
    return "\n".join(lines)
