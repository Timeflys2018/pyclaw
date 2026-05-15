from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from pyclaw.core.agent.compaction import estimate_messages_tokens, take_checkpoint
from pyclaw.core.agent.incomplete_turn import classify_turn, retry_message_for
from pyclaw.core.agent.llm import (
    LLMClient,
    LLMError,
    LLMResponse,
    LLMUsage,
    finalize_tool_calls,
    format_vision_capable_models,
    merge_tool_call_deltas,
    model_supports_input,
)
from pyclaw.core.agent.run_control import RunControl
from pyclaw.core.agent.runtime_util import (
    AgentAbortedError,
    AgentTimeoutError,
    is_abort_set,
    iterate_with_deadline,
)
from pyclaw.core.agent.system_prompt import (
    PromptInputs,
    build_frozen_prefix,
    build_per_turn_suffix,
)
from pyclaw.core.agent.tool_result_truncation import (
    resolve_max_output_chars,
    truncate_tool_result,
)
from pyclaw.core.agent.tools.registry import (
    ToolContext,
    ToolRegistry,
    execute_tool_calls,
    tool_result_to_llm_content,
)
from pyclaw.core.context_engine import ContextEngine, DefaultContextEngine
from pyclaw.core.hooks import (
    CompactionContext,
    HookRegistry,
    PermissionTier,
    ResponseObservation,
    SkillProvider,
    ToolApprovalHook,
)
from pyclaw.infra.task_manager import TaskManager
from pyclaw.models import (
    AgentRunConfig,
    Done,
    ErrorEvent,
    MessageEntry,
    SessionHeader,
    SessionTree,
    TextBlock,
    TextChunk,
    ToolApprovalRequest,
    ToolCallEnd,
    ToolCallStart,
    ToolResult,
    generate_entry_id,
    now_iso,
)
from pyclaw.storage.session.base import InMemorySessionStore, SessionStore
from pyclaw.storage.workspace.base import WorkspaceStore

logger = logging.getLogger(__name__)


@dataclass
class AgentRunnerDeps:
    llm: LLMClient
    tools: ToolRegistry
    context_engine: ContextEngine = field(default_factory=DefaultContextEngine)
    hooks: HookRegistry = field(default_factory=HookRegistry)
    session_store: SessionStore = field(default_factory=InMemorySessionStore)
    config: AgentRunConfig = field(default_factory=AgentRunConfig)
    workspace_store: WorkspaceStore | None = field(default=None)
    skill_provider: SkillProvider | None = field(default=None)
    tool_approval_hook: ToolApprovalHook | None = field(default=None)
    task_manager: TaskManager | None = field(default=None)
    lock_manager: Any = field(default=None)
    audit_logger: Any = field(default=None)
    channel: str = "web"


@dataclass
class RunRequest:
    session_id: str
    workspace_id: str
    agent_id: str
    user_message: str
    model: str | None = None
    tool_context_extras: dict[str, Any] = field(default_factory=dict)
    attachments: list[Any] = field(default_factory=list)
    extra_system: str = ""
    permission_tier_override: PermissionTier | None = None


@dataclass
class RunResult:
    final_text: str
    iterations: int
    terminated_by: str


async def ensure_session(
    deps: AgentRunnerDeps,
    *,
    session_id: str,
    workspace_id: str,
    agent_id: str,
) -> SessionTree:
    existing = await deps.session_store.load(session_id)
    if existing is not None:
        return existing
    header = SessionHeader(id=session_id, workspace_id=workspace_id, agent_id=agent_id)
    tree = SessionTree(header=header)
    await deps.session_store.save_header(tree)
    return tree


async def _append(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    entry: MessageEntry,
) -> None:
    tree.entries[entry.id] = entry
    tree.order.append(entry.id)
    tree.leaf_id = entry.id
    await deps.session_store.append_entry(tree.header.id, entry, leaf_id=entry.id)
    await deps.context_engine.ingest(tree.header.id, _entry_to_llm_dict(entry))


def _entry_to_llm_dict(entry: MessageEntry) -> dict[str, Any]:
    out: dict[str, Any] = {"role": entry.role, "content": entry.content}
    if entry.tool_calls:
        out["tool_calls"] = entry.tool_calls
    if entry.tool_call_id:
        out["tool_call_id"] = entry.tool_call_id
    return out


async def _persist_partial_assistant(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    text: str,
) -> None:
    """Persist already-streamed assistant text as a partial=True MessageEntry.

    Best-effort persistence: failures (e.g., Redis connection drop in
    session_store.append_entry) are logged at WARNING and SHALL NOT propagate.
    The caller's `yield ErrorEvent(...)` is the must-deliver cross-layer
    signal; partial persistence is data enrichment that can be sacrificed
    under storage pressure.

    `except Exception` is deliberate — `asyncio.CancelledError` and
    `KeyboardInterrupt` inherit from `BaseException` (Python 3.8+) so they
    propagate normally, which is correct (cancellation must not be swallowed).

    Caller MUST gate on non-empty text. Caller MUST only invoke from
    Bucket B paths (mid-stream / LLM-raised ErrorEvent exits).
    """
    if not text:
        return
    entry = MessageEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        role="assistant",
        content=text,
        partial=True,
    )
    try:
        await _append(deps, tree, entry)
    except Exception:
        logger.warning(
            "failed to persist partial assistant content (best-effort); "
            "ErrorEvent dispatch will still proceed",
            exc_info=True,
        )


async def run_agent(
    request: RunRequest,
    deps: AgentRunnerDeps,
    *,
    tool_workspace_path,
) -> RunResult:
    final_text = ""
    iterations = 0
    terminated_by = "done"
    async for event in run_agent_stream(request, deps, tool_workspace_path=tool_workspace_path):
        if isinstance(event, Done):
            final_text = event.final_message
            terminated_by = "done"
        elif isinstance(event, ErrorEvent):
            terminated_by = event.error_code
        elif isinstance(event, ToolCallEnd):
            iterations += 1
    return RunResult(final_text=final_text, iterations=iterations, terminated_by=terminated_by)


async def run_agent_stream(
    request: RunRequest,
    deps: AgentRunnerDeps,
    *,
    tool_workspace_path,
    control: RunControl | None = None,
    abort: asyncio.Event | None = None,
) -> AsyncIterator[Any]:
    if control is None:
        control = RunControl(abort_event=abort) if abort is not None else RunControl()
    abort_event = control.abort_event
    run_deadline_s = deps.config.timeouts.run_seconds
    run_started = time.monotonic()
    terminated_by = "done"
    await deps.hooks.notify_run_start(request.session_id, control)

    try:

        def _run_timed_out() -> bool:
            return run_deadline_s > 0 and (time.monotonic() - run_started) > run_deadline_s

        tree = await ensure_session(
            deps,
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
        )

        effective_model = request.model or tree.header.model_override or deps.llm.default_model

        if request.attachments and getattr(deps.llm, "_providers", None):
            providers = deps.llm._providers
            if not model_supports_input(
                effective_model,
                providers,
                "image",
                default_provider=getattr(deps.llm, "_default_provider", None),
                unknown_prefix_policy="fail",
            ):
                vision_models_str = format_vision_capable_models(providers)
                yield ErrorEvent(
                    error_code="vision_not_support",
                    message=(
                        f"Model '{effective_model}' does not have image input capability. "
                        f"Available vision-capable models: {vision_models_str}. "
                        f"Use /model <model_id> to switch."
                    ),
                )
                return

        user_entry_content: Any
        if request.attachments:
            blocks: list[Any] = list(request.attachments)
            if request.user_message and request.user_message.strip():
                blocks.append(TextBlock(type="text", text=request.user_message))
            user_entry_content = blocks
        else:
            user_entry_content = request.user_message

        user_entry = MessageEntry(
            id=generate_entry_id(set(tree.entries.keys())),
            parent_id=tree.leaf_id,
            timestamp=now_iso(),
            role="user",
            content=user_entry_content,
        )
        await _append(deps, tree, user_entry)

        tool_ctx = ToolContext(
            workspace_id=request.workspace_id,
            workspace_path=tool_workspace_path,
            session_id=request.session_id,
            abort=abort_event,
            extras=request.tool_context_extras,
        )

        skills_prompt_str: str | None = None
        if tool_workspace_path and deps.skill_provider:
            skills_prompt_str = deps.skill_provider.resolve_skills_prompt(str(tool_workspace_path))

        tool_summaries = [
            (t.name, t.description)
            for t in (deps.tools.get(n) for n in deps.tools.names())
            if t is not None
        ]
        prompt_inputs = PromptInputs(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
            model=effective_model,
            tools=tool_summaries,
            skills_prompt=skills_prompt_str,
            workspace_path=str(tool_workspace_path),
        )

        bootstrap_text: str | None = None
        l1_snapshot_text: str | None = None
        get_bootstrap = getattr(deps.context_engine, "get_bootstrap", None)
        if get_bootstrap is not None:
            try:
                bootstrap_text = await get_bootstrap(request.session_id)
            except Exception:
                logger.warning("get_bootstrap failed", exc_info=True)
        get_l1 = getattr(deps.context_engine, "get_l1_snapshot", None)
        if get_l1 is not None:
            try:
                l1_entries = await get_l1(request.session_id)
                if l1_entries:
                    l1_snapshot_text = _format_l1_snapshot(l1_entries)
            except Exception:
                logger.warning("get_l1_snapshot failed", exc_info=True)

        frozen_result = build_frozen_prefix(
            prompt_inputs,
            budget=deps.config.prompt_budget.system_zone_tokens,
            bootstrap=bootstrap_text,
            l1_snapshot=l1_snapshot_text,
        )

        model_max_output: int | None = None
        try:
            from litellm import get_model_info

            _info = get_model_info(effective_model)
            model_max_output = _info.get("max_output_tokens") or _info.get("max_tokens")
        except Exception:
            logger.debug("get_model_info failed for %s, using ratio fallback", effective_model)

        history_budget = deps.config.prompt_budget.compute_history_budget(
            deps.config.context_window, model_max_output=model_max_output
        )

        iteration = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_creation_tokens = 0
        total_cache_read_tokens = 0
        final_text = ""
        retry_counts: dict[str, int] = {"planning": 0, "reasoning": 0, "empty": 0}
        unknown_tool_name: str | None = None
        unknown_tool_count = 0
        unknown_tool_warned = False

        while iteration < deps.config.max_iterations:
            if _run_timed_out():
                terminated_by = "timeout"
                yield ErrorEvent(
                    error_code="timeout",
                    message=f"run exceeded {run_deadline_s}s run_seconds",
                )
                return
            if is_abort_set(abort_event):
                terminated_by = "aborted"
                yield ErrorEvent(error_code="aborted", message="run aborted")
                return
            iteration += 1

            per_turn_result = await build_per_turn_suffix(
                prompt_inputs, hooks=deps.hooks, user_prompt=request.user_message
            )

            base_messages = tree.build_session_context()
            assembled = await deps.context_engine.assemble(
                session_id=request.session_id,
                messages=base_messages,
                token_budget=history_budget,
                prompt=request.user_message,
            )

            other_system_parts: list[str] = [per_turn_result.text]
            if assembled.system_prompt_addition:
                other_system_parts.append(assembled.system_prompt_addition)
            if request.extra_system:
                other_system_parts.append(request.extra_system)
            effective_system = _build_effective_system(
                frozen_text=frozen_result.text,
                other_parts=other_system_parts,
                model=effective_model,
            )

            if history_budget > 0 and estimate_messages_tokens(assembled.messages) > history_budget:
                pretrim_outcome = await _try_compaction(
                    deps, tree, request, base_messages, history_budget, abort_event, force=True
                )
                if not pretrim_outcome.ok:
                    terminated_by = pretrim_outcome.error_code or "compaction_failed"
                    yield ErrorEvent(
                        error_code=pretrim_outcome.error_code or "compaction_failed",
                        message=pretrim_outcome.error_message or "compaction failed",
                    )
                    return
                if pretrim_outcome.compacted:
                    continue

            remaining_run = _remaining_run_seconds(run_deadline_s, run_started)
            if remaining_run is not None and remaining_run <= 0:
                terminated_by = "timeout"
                yield ErrorEvent(error_code="timeout", message="run exceeded run_seconds")
                return

            text_parts: list[str] = []
            tool_calls_buffer: dict[int, dict[str, Any]] = {}
            finish_reason: str | None = None
            stream_usage = LLMUsage()

            try:
                stream_iter = deps.llm.stream(
                    messages=assembled.messages,
                    model=effective_model,
                    tools=deps.tools.list_for_llm() or None,
                    system=effective_system,
                    idle_seconds=deps.config.timeouts.idle_seconds,
                    abort_event=abort_event,
                )
                guarded_iter = iterate_with_deadline(
                    stream_iter,
                    deadline_s=remaining_run
                    if remaining_run is not None and remaining_run > 0
                    else 0.0,
                    abort_event=abort_event,
                    kind="run",
                )
                stream_start = time.monotonic()
                async for chunk in guarded_iter:
                    if _run_timed_out():
                        terminated_by = "timeout"
                        if text_parts:
                            await _persist_partial_assistant(deps, tree, "".join(text_parts))
                        yield ErrorEvent(
                            error_code="timeout",
                            message=f"run exceeded {run_deadline_s}s run_seconds during stream",
                        )
                        return
                    if is_abort_set(abort_event):
                        terminated_by = "aborted"
                        if text_parts:
                            await _persist_partial_assistant(deps, tree, "".join(text_parts))
                        yield ErrorEvent(
                            error_code="aborted", message="run aborted during llm stream"
                        )
                        return
                    if chunk.text_delta:
                        text_parts.append(chunk.text_delta)
                        yield TextChunk(text=chunk.text_delta)
                    if chunk.tool_call_deltas:
                        merge_tool_call_deltas(tool_calls_buffer, chunk.tool_call_deltas)
                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason
                    if chunk.usage:
                        stream_usage = chunk.usage
                _stream_elapsed = time.monotonic() - stream_start
            except AgentTimeoutError as te:
                terminated_by = "timeout"
                if text_parts:
                    await _persist_partial_assistant(deps, tree, "".join(text_parts))
                yield ErrorEvent(error_code="timeout", message=str(te))
                return
            except AgentAbortedError:
                terminated_by = "aborted"
                if text_parts:
                    await _persist_partial_assistant(deps, tree, "".join(text_parts))
                yield ErrorEvent(error_code="aborted", message="run aborted during llm call")
                return
            except LLMError as exc:
                logger.error("LLM error: code=%s message=%s", exc.code, str(exc)[:500])
                if exc.code == "context_overflow":
                    outcome = await _try_compaction(
                        deps, tree, request, base_messages, history_budget, abort_event, force=True
                    )
                    if not outcome.ok:
                        terminated_by = outcome.error_code or "compaction_failed"
                        if text_parts:
                            await _persist_partial_assistant(deps, tree, "".join(text_parts))
                        yield ErrorEvent(
                            error_code=outcome.error_code or "compaction_failed",
                            message=outcome.error_message or "compaction failed",
                        )
                        return
                    if outcome.compacted:
                        continue
                terminated_by = exc.code
                if text_parts:
                    await _persist_partial_assistant(deps, tree, "".join(text_parts))
                yield ErrorEvent(error_code=exc.code, message=str(exc))
                return

            assembled_text = "".join(text_parts)
            assembled_tool_calls = finalize_tool_calls(tool_calls_buffer)
            response = LLMResponse(
                text=assembled_text,
                tool_calls=assembled_tool_calls,
                usage=stream_usage,
                finish_reason=finish_reason,
            )

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens
            total_cache_creation_tokens += response.usage.cache_creation_input_tokens
            total_cache_read_tokens += response.usage.cache_read_input_tokens

            frozen_tokens = sum(frozen_result.token_breakdown.values())
            bootstrap_tokens = frozen_result.token_breakdown.get("bootstrap", 0)
            l1_tokens = frozen_result.token_breakdown.get("l1_snapshot", 0)
            memory_context_tokens = len(assembled.system_prompt_addition or "") // 4
            per_turn_tokens = sum(per_turn_result.token_breakdown.values())
            history_tokens = estimate_messages_tokens(assembled.messages)
            logger.info(
                "token_usage turn=%d frozen=%d bootstrap=%d l1=%d memory_ctx=%d per_turn=%d history=%d "
                "input=%d output=%d budget_remaining=%d cache_creation=%d cache_read=%d",
                iteration,
                frozen_tokens,
                bootstrap_tokens,
                l1_tokens,
                memory_context_tokens,
                per_turn_tokens,
                history_tokens,
                response.usage.input_tokens,
                response.usage.output_tokens,
                max(0, history_budget - history_tokens),
                response.usage.cache_creation_input_tokens,
                response.usage.cache_read_input_tokens,
            )

            if not response.tool_calls:
                classification = classify_turn(
                    text=response.text,
                    tool_calls=response.tool_calls,
                )
                retry_limit = _retry_limit_for(classification, deps.config)
                if (
                    classification in ("planning", "reasoning", "empty")
                    and retry_limit > 0
                    and retry_counts[classification] < retry_limit
                ):
                    retry_counts[classification] += 1
                    assistant_entry = MessageEntry(
                        id=generate_entry_id(set(tree.entries.keys())),
                        parent_id=tree.leaf_id,
                        timestamp=now_iso(),
                        role="assistant",
                        content=response.text,
                    )
                    await _append(deps, tree, assistant_entry)

                    retry_prompt = retry_message_for(classification)
                    if retry_prompt is not None:
                        retry_entry = MessageEntry(
                            id=generate_entry_id(set(tree.entries.keys())),
                            parent_id=tree.leaf_id,
                            timestamp=now_iso(),
                            role="user",
                            content=retry_prompt,
                        )
                        await _append(deps, tree, retry_entry)
                    continue

                assistant_entry = MessageEntry(
                    id=generate_entry_id(set(tree.entries.keys())),
                    parent_id=tree.leaf_id,
                    timestamp=now_iso(),
                    role="assistant",
                    content=response.text,
                )
                await _append(deps, tree, assistant_entry)
                await deps.hooks.notify_response(
                    ResponseObservation(
                        session_id=request.session_id,
                        assistant_text=response.text,
                        tool_calls=[],
                    )
                )
                await deps.context_engine.after_turn(
                    request.session_id, tree.build_session_context()
                )
                final_text = response.text
                terminated_by = "done"
                yield Done(
                    final_message=final_text,
                    usage={
                        "input": total_input_tokens,
                        "output": total_output_tokens,
                        "cache_creation": total_cache_creation_tokens,
                        "cache_read": total_cache_read_tokens,
                    },
                )
                return

            assistant_entry = MessageEntry(
                id=generate_entry_id(set(tree.entries.keys())),
                parent_id=tree.leaf_id,
                timestamp=now_iso(),
                role="assistant",
                content=response.text,
                tool_calls=response.tool_calls,
            )
            await _append(deps, tree, assistant_entry)
            await deps.hooks.notify_response(
                ResponseObservation(
                    session_id=request.session_id,
                    assistant_text=response.text,
                    tool_calls=response.tool_calls,
                )
            )

            (
                unknown_tool_name,
                unknown_tool_count,
                unknown_tool_warned,
                loop_action,
                loop_guidance,
            ) = _update_tool_loop_state(
                response.tool_calls,
                deps.tools,
                unknown_tool_name,
                unknown_tool_count,
                unknown_tool_warned,
                deps.config.retry.unknown_tool_threshold,
            )

            if loop_action == "terminate":
                terminated_by = "tool_loop"
                yield ErrorEvent(
                    error_code="tool_loop",
                    message=f"tool {unknown_tool_name!r} called after unknown-tool guidance",
                )
                return

            if loop_action == "warn" and loop_guidance is not None:
                guidance_entry = MessageEntry(
                    id=generate_entry_id(set(tree.entries.keys())),
                    parent_id=tree.leaf_id,
                    timestamp=now_iso(),
                    role="user",
                    content=loop_guidance,
                )
                await _append(deps, tree, guidance_entry)
                continue

            parsed_calls: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
            for call in response.tool_calls:
                fn = (call or {}).get("function") or {}
                raw_args = fn.get("arguments") or {}
                if isinstance(raw_args, str):
                    import json as _json

                    try:
                        raw_args = _json.loads(raw_args)
                    except Exception:
                        raw_args = {"_raw": raw_args}
                parsed_calls.append((call, fn.get("name", "") or "", raw_args))
                yield ToolCallStart(
                    tool_call_id=call.get("id", ""),
                    name=fn.get("name", "") or "",
                    arguments=raw_args,
                )

            effective_tier: PermissionTier = request.permission_tier_override or "approval"

            denied_ids: dict[str, str] = {}

            if effective_tier == "read-only":
                for call, tool_name, _ in parsed_calls:
                    tool_obj = deps.tools.get(tool_name) if tool_name else None
                    cls = getattr(tool_obj, "tool_class", "write")
                    cid = call.get("id", "")
                    if cls == "write":
                        denied_ids[cid] = (
                            f"Tool '{tool_name}' is not available in read-only mode. "
                            "(Mode can be changed in the input footer.)"
                        )
                        _emit_runner_audit(
                            deps, request.session_id, deps.channel,
                            tool_name, cid, effective_tier,
                            decision="deny", decided_by="auto:read-only",
                        )
                    else:
                        _emit_runner_audit(
                            deps, request.session_id, deps.channel,
                            tool_name, cid, effective_tier,
                            decision="approve", decided_by="auto:read-only",
                        )

            elif effective_tier == "yolo":
                for call, tool_name, _ in parsed_calls:
                    _emit_runner_audit(
                        deps, request.session_id, deps.channel,
                        tool_name, call.get("id", ""), effective_tier,
                        decision="approve", decided_by="auto:yolo",
                    )

            elif effective_tier == "approval" and deps.tool_approval_hook is not None:
                for call, tool_name, raw_args in parsed_calls:
                    yield ToolApprovalRequest(
                        tool_call_id=call.get("id", ""),
                        tool_name=tool_name,
                        args=raw_args,
                    )

                decisions = await deps.tool_approval_hook.before_tool_execution(
                    [{"id": c.get("id", ""), "name": tn, "args": ra} for c, tn, ra in parsed_calls],
                    session_id=request.session_id,
                    tier=effective_tier,
                )

                for idx, decision in enumerate(decisions):
                    if decision == "deny":
                        cid = parsed_calls[idx][0].get("id", "")
                        tname = parsed_calls[idx][1]
                        denied_ids[cid] = f"Tool '{tname}' was denied by approval hook."

            if denied_ids:
                response.tool_calls = [
                    tc for tc in response.tool_calls if tc.get("id", "") not in denied_ids
                ]
                for call, _tool_name, _ in parsed_calls:
                    cid = call.get("id", "")
                    if cid in denied_ids:
                        denied_result = ToolResult(
                            tool_call_id=cid,
                            content=[TextBlock(text=denied_ids[cid])],
                            is_error=True,
                        )
                        denied_entry = MessageEntry(
                            id=generate_entry_id(set(tree.entries.keys())),
                            parent_id=tree.leaf_id,
                            timestamp=now_iso(),
                            role="tool",
                            content=tool_result_to_llm_content(denied_result),
                            tool_call_id=cid,
                        )
                        await _append(deps, tree, denied_entry)
                        yield ToolCallEnd(tool_call_id=cid, result=denied_result)
                if not response.tool_calls:
                    continue

            results: list[ToolResult] = await execute_tool_calls(
                deps.tools,
                response.tool_calls,
                tool_ctx,
                default_tool_timeout_s=deps.config.timeouts.tool_seconds,
            )

            truncated_results: list[ToolResult] = []
            for call, result in zip(response.tool_calls, results, strict=False):
                tool_name = ((call or {}).get("function") or {}).get("name") or ""
                tool_obj = deps.tools.get(tool_name) if tool_name else None
                cap = (
                    resolve_max_output_chars(tool_obj, deps.config.tools.max_output_chars)
                    if tool_obj is not None
                    else deps.config.tools.max_output_chars
                )
                truncated_results.append(truncate_tool_result(result, cap))
            results = truncated_results

            for call, result in zip(response.tool_calls, results, strict=False):
                tool_entry = MessageEntry(
                    id=generate_entry_id(set(tree.entries.keys())),
                    parent_id=tree.leaf_id,
                    timestamp=now_iso(),
                    role="tool",
                    content=tool_result_to_llm_content(result),
                    tool_call_id=call.get("id", ""),
                )
                await _append(deps, tree, tool_entry)
                yield ToolCallEnd(tool_call_id=call.get("id", ""), result=result)

            retry_counts = {"planning": 0, "reasoning": 0, "empty": 0}

        terminated_by = "max_iterations"
        yield ErrorEvent(
            error_code="max_iterations",
            message=f"reached max_iterations={deps.config.max_iterations}",
        )
    finally:
        await deps.hooks.notify_run_end(request.session_id, terminated_by)


@dataclass
class _CompactionOutcome:
    ok: bool
    compacted: bool
    error_code: str | None = None
    error_message: str | None = None
    result: Any = None


async def _try_compaction(
    deps: AgentRunnerDeps,
    tree: SessionTree,
    request: RunRequest,
    base_messages: list[Any],
    history_budget: int,
    abort_event: asyncio.Event,
    *,
    force: bool,
) -> _CompactionOutcome:
    compaction_ctx = CompactionContext(
        session_id=request.session_id,
        workspace_id=request.workspace_id,
        agent_id=request.agent_id,
        message_count=len(base_messages),
        tokens_before=estimate_messages_tokens(base_messages),
    )
    checkpoint = take_checkpoint(tree)
    await deps.hooks.notify_before_compaction(compaction_ctx)

    try:
        compact_result = await deps.context_engine.compact(
            session_id=request.session_id,
            messages=base_messages,
            token_budget=history_budget,
            force=force,
            abort_event=abort_event,
            model=deps.config.compaction.model,
        )
    except Exception as compact_exc:
        checkpoint.restore_into(tree)
        return _CompactionOutcome(
            ok=False,
            compacted=False,
            error_code="compaction_failed",
            error_message=f"compaction raised {type(compact_exc).__name__}: {compact_exc}",
        )

    compaction_ctx.tokens_before = compact_result.tokens_before
    await deps.hooks.notify_after_compaction(compaction_ctx, compact_result)

    if not compact_result.ok:
        checkpoint.restore_into(tree)
        return _CompactionOutcome(
            ok=False,
            compacted=False,
            error_code=compact_result.reason_code or "compaction_failed",
            error_message=compact_result.reason or "compaction failed",
            result=compact_result,
        )

    if compact_result.compacted and compact_result.summary:
        from pyclaw.models import CompactionEntry

        comp_entry = CompactionEntry(
            id=generate_entry_id(set(tree.entries.keys())),
            parent_id=tree.leaf_id,
            summary=compact_result.summary,
            first_kept_entry_id=tree.leaf_id or "",
            tokens_before=compact_result.tokens_before,
        )
        tree.entries[comp_entry.id] = comp_entry
        tree.order.append(comp_entry.id)
        tree.leaf_id = comp_entry.id
        await deps.session_store.append_entry(tree.header.id, comp_entry, leaf_id=comp_entry.id)
        return _CompactionOutcome(ok=True, compacted=True, result=compact_result)

    return _CompactionOutcome(ok=True, compacted=False, result=compact_result)


def _format_l1_snapshot(entries: list[Any]) -> str:
    if not entries:
        return ""
    lines = ["<memory_index>"]
    for entry in entries:
        # 防御性过滤：跳过非 active 状态的条目（L1 evict 失败时的兜底）
        status = getattr(entry, "status", "active")
        if isinstance(entry, dict):
            status = entry.get("status", "active")
        if status != "active":
            continue
        content = getattr(entry, "content", None)
        if content is None and isinstance(entry, dict):
            content = entry.get("content", "")
        if content:
            lines.append(f"- {content}")
    lines.append("</memory_index>")
    return "\n".join(lines)


def _remaining_run_seconds(run_deadline_s: float, run_started: float) -> float | None:
    if run_deadline_s <= 0:
        return None
    remaining = run_deadline_s - (time.monotonic() - run_started)
    return remaining


def _retry_limit_for(classification: str, config: AgentRunConfig) -> int:
    if classification == "planning":
        return config.retry.planning_only_limit
    if classification == "reasoning":
        return config.retry.reasoning_only_limit
    if classification == "empty":
        return config.retry.empty_response_limit
    return 0


def _update_tool_loop_state(
    tool_calls: list[dict[str, Any]],
    registry: ToolRegistry,
    current_unknown: str | None,
    count: int,
    warned: bool,
    threshold: int,
) -> tuple[str | None, int, bool, str, str | None]:
    if threshold <= 0 or not tool_calls:
        return None, 0, False, "proceed", None

    names = []
    for call in tool_calls:
        fn = (call or {}).get("function") or {}
        name = fn.get("name", "") or ""
        names.append(name)

    unknown_names_in_turn = [n for n in names if n and n not in registry]

    if not unknown_names_in_turn:
        return None, 0, False, "proceed", None

    if any(n in registry for n in names):
        pass

    first_unknown = unknown_names_in_turn[0]

    if current_unknown is not None and first_unknown != current_unknown:
        count = 0
        warned = False

    current_unknown = first_unknown
    count += 1

    if warned:
        return current_unknown, count, warned, "terminate", None

    if count >= threshold:
        available = ", ".join(sorted(registry.names())) or "(none)"
        guidance = (
            f"You called `{current_unknown}` which does not exist. "
            f"Available tools: [{available}]. Stop calling `{current_unknown}`."
        )
        return current_unknown, count, True, "warn", guidance

    return current_unknown, count, warned, "proceed", None


_ANTHROPIC_PREFIXES = (
    "anthropic/",
    "claude-",
    "bedrock/anthropic.",
    "vertex_ai/claude-",
)
_MIN_CACHE_TOKENS = 1024


def _is_anthropic_model(model: str) -> bool:
    m = model.lower()
    return any(m.startswith(p) for p in _ANTHROPIC_PREFIXES)


def _emit_runner_audit(
    deps: AgentRunnerDeps,
    session_id: str,
    channel: str,
    tool_name: str,
    tool_call_id: str,
    tier: PermissionTier,
    *,
    decision: str,
    decided_by: str,
) -> None:
    audit = getattr(deps, "audit_logger", None)
    if audit is None:
        return
    try:
        audit.log_decision(
            conv_id=session_id,
            session_id=session_id,
            channel=channel,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            tier=tier,
            decision=decision,
            decided_by=decided_by,
        )
    except Exception:
        logger.warning("audit log emit failed", exc_info=True)


def _build_effective_system(
    frozen_text: str,
    other_parts: list[str],
    model: str,
) -> str | list[dict[str, Any]]:
    rest = "\n\n".join(p for p in other_parts if p)
    if not _is_anthropic_model(model):
        full = "\n\n".join([frozen_text, rest]) if rest else frozen_text
        return full
    frozen_tokens_est = len(frozen_text) // 4
    if frozen_tokens_est < _MIN_CACHE_TOKENS:
        full = "\n\n".join([frozen_text, rest]) if rest else frozen_text
        return full
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": frozen_text, "cache_control": {"type": "ephemeral"}},
    ]
    if rest:
        blocks.append({"type": "text", "text": rest})
    return blocks
