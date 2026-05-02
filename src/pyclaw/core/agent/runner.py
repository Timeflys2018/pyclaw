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
    merge_tool_call_deltas,
)
from pyclaw.core.agent.tool_result_truncation import (
    resolve_max_output_chars,
    truncate_tool_result,
)
from pyclaw.core.hooks import CompactionContext
from pyclaw.core.agent.runtime_util import (
    AgentAbortedError,
    AgentTimeoutError,
    is_abort_set,
    iterate_with_deadline,
)
from pyclaw.core.agent.system_prompt import PromptInputs, build_system_prompt
from pyclaw.core.agent.tools.registry import (
    ToolContext,
    ToolRegistry,
    execute_tool_calls,
    tool_result_to_llm_content,
)
from pyclaw.core.context_engine import ContextEngine, DefaultContextEngine
from pyclaw.core.hooks import HookRegistry, ResponseObservation, SkillProvider, ToolApprovalHook
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
    abort: asyncio.Event | None = None,
) -> AsyncIterator[Any]:
    abort_event = abort if abort is not None else asyncio.Event()
    run_deadline_s = deps.config.timeouts.run_seconds
    run_started = time.monotonic()

    def _run_timed_out() -> bool:
        return run_deadline_s > 0 and (time.monotonic() - run_started) > run_deadline_s

    tree = await ensure_session(
        deps,
        session_id=request.session_id,
        workspace_id=request.workspace_id,
        agent_id=request.agent_id,
    )

    user_entry_content: Any
    if request.attachments:
        user_entry_content = [
            *request.attachments,
            TextBlock(type="text", text=request.user_message),
        ]
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

    tool_summaries = [(t.name, t.description) for t in (deps.tools.get(n) for n in deps.tools.names()) if t is not None]
    system_prompt = await build_system_prompt(
        PromptInputs(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
            model=request.model or deps.llm.default_model,
            tools=tool_summaries,
            skills_prompt=skills_prompt_str,
            workspace_path=str(tool_workspace_path),
        ),
        hooks=deps.hooks,
        user_prompt=request.user_message,
    )
    if request.extra_system:
        system_prompt = f"{system_prompt}\n\n{request.extra_system}"

    iteration = 0
    total_input_tokens = 0
    total_output_tokens = 0
    final_text = ""
    retry_counts: dict[str, int] = {"planning": 0, "reasoning": 0, "empty": 0}
    unknown_tool_name: str | None = None
    unknown_tool_count = 0
    unknown_tool_warned = False

    while iteration < deps.config.max_iterations:
        if _run_timed_out():
            yield ErrorEvent(
                error_code="timeout",
                message=f"run exceeded {run_deadline_s}s run_seconds",
            )
            return
        if is_abort_set(abort_event):
            yield ErrorEvent(error_code="aborted", message="run aborted")
            return
        iteration += 1

        base_messages = tree.build_session_context()
        assembled = await deps.context_engine.assemble(
            session_id=request.session_id,
            messages=base_messages,
            token_budget=deps.config.context_window,
            prompt=request.user_message,
        )
        effective_system = system_prompt
        if assembled.system_prompt_addition:
            effective_system = f"{system_prompt}\n\n{assembled.system_prompt_addition}"

        remaining_run = _remaining_run_seconds(run_deadline_s, run_started)
        if remaining_run is not None and remaining_run <= 0:
            yield ErrorEvent(error_code="timeout", message="run exceeded run_seconds")
            return

        text_parts: list[str] = []
        tool_calls_buffer: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        stream_usage = LLMUsage()

        try:
            stream_iter = deps.llm.stream(
                messages=assembled.messages,
                model=request.model,
                tools=deps.tools.list_for_llm() or None,
                system=effective_system,
                idle_seconds=deps.config.timeouts.idle_seconds,
                abort_event=abort_event,
            )
            guarded_iter = iterate_with_deadline(
                stream_iter,
                deadline_s=remaining_run if remaining_run is not None and remaining_run > 0 else 0.0,
                abort_event=abort_event,
                kind="run",
            )
            stream_start = time.monotonic()
            async for chunk in guarded_iter:
                if _run_timed_out():
                    yield ErrorEvent(
                        error_code="timeout",
                        message=f"run exceeded {run_deadline_s}s run_seconds during stream",
                    )
                    return
                if is_abort_set(abort_event):
                    yield ErrorEvent(error_code="aborted", message="run aborted during llm stream")
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
            yield ErrorEvent(error_code="timeout", message=str(te))
            return
        except AgentAbortedError:
            yield ErrorEvent(error_code="aborted", message="run aborted during llm call")
            return
        except LLMError as exc:
            if exc.code == "context_overflow":
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
                        token_budget=deps.config.context_window,
                        force=True,
                        abort_event=abort_event,
                        model=deps.config.compaction.model,
                    )
                except Exception as compact_exc:
                    checkpoint.restore_into(tree)
                    yield ErrorEvent(
                        error_code="compaction_failed",
                        message=f"compaction raised {type(compact_exc).__name__}: {compact_exc}",
                    )
                    return

                compaction_ctx.tokens_before = compact_result.tokens_before
                await deps.hooks.notify_after_compaction(compaction_ctx, compact_result)

                if not compact_result.ok:
                    checkpoint.restore_into(tree)
                    yield ErrorEvent(
                        error_code=compact_result.reason_code or "compaction_failed",
                        message=compact_result.reason or "compaction failed",
                    )
                    return

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
                    await deps.session_store.append_entry(
                        tree.header.id, comp_entry, leaf_id=comp_entry.id
                    )
                    continue
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
            yield Done(
                final_message=final_text,
                usage={"input": total_input_tokens, "output": total_output_tokens},
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

        parsed_calls: list[tuple[dict, str, dict]] = []
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

        if deps.tool_approval_hook is not None:
            for call, tool_name, raw_args in parsed_calls:
                yield ToolApprovalRequest(
                    tool_call_id=call.get("id", ""),
                    tool_name=tool_name,
                    args=raw_args,
                )

            decisions = await deps.tool_approval_hook.before_tool_execution(
                [
                    {"id": c.get("id", ""), "name": tn, "args": ra}
                    for c, tn, ra in parsed_calls
                ],
                session_id=request.session_id,
            )

            denied_ids: set[str] = set()
            for idx, decision in enumerate(decisions):
                if decision == "deny":
                    denied_ids.add(parsed_calls[idx][0].get("id", ""))

            if denied_ids:
                response.tool_calls = [
                    tc for tc in response.tool_calls
                    if tc.get("id", "") not in denied_ids
                ]
                if not response.tool_calls:
                    for call, tool_name, _ in parsed_calls:
                        cid = call.get("id", "")
                        if cid in denied_ids:
                            denied_result = ToolResult(
                                tool_call_id=cid,
                                content=[TextBlock(text=f"Tool '{tool_name}' was denied by approval hook.")],
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

    yield ErrorEvent(
        error_code="max_iterations",
        message=f"reached max_iterations={deps.config.max_iterations}",
    )


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
