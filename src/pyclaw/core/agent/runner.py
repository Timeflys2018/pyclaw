from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from pyclaw.core.agent.llm import LLMClient, LLMError, LLMResponse
from pyclaw.core.agent.runtime_util import (
    AgentAbortedError,
    AgentTimeoutError,
    is_abort_set,
)
from pyclaw.core.agent.system_prompt import PromptInputs, build_system_prompt
from pyclaw.core.agent.tools.registry import (
    ToolContext,
    ToolRegistry,
    execute_tool_calls,
    tool_result_to_llm_content,
)
from pyclaw.core.context_engine import ContextEngine, DefaultContextEngine
from pyclaw.core.hooks import HookRegistry, ResponseObservation
from pyclaw.models import (
    AgentRunConfig,
    Done,
    ErrorEvent,
    MessageEntry,
    SessionHeader,
    SessionTree,
    TextChunk,
    ToolCallEnd,
    ToolCallStart,
    ToolResult,
    generate_entry_id,
    now_iso,
)
from pyclaw.storage.session.base import InMemorySessionStore, SessionStore


@dataclass
class AgentRunnerDeps:
    llm: LLMClient
    tools: ToolRegistry
    context_engine: ContextEngine = field(default_factory=DefaultContextEngine)
    hooks: HookRegistry = field(default_factory=HookRegistry)
    session_store: SessionStore = field(default_factory=InMemorySessionStore)
    config: AgentRunConfig = field(default_factory=AgentRunConfig)


@dataclass
class RunRequest:
    session_id: str
    workspace_id: str
    agent_id: str
    user_message: str
    model: str | None = None
    tool_context_extras: dict[str, Any] = field(default_factory=dict)


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

    user_entry = MessageEntry(
        id=generate_entry_id(set(tree.entries.keys())),
        parent_id=tree.leaf_id,
        timestamp=now_iso(),
        role="user",
        content=request.user_message,
    )
    await _append(deps, tree, user_entry)

    tool_ctx = ToolContext(
        workspace_id=request.workspace_id,
        workspace_path=tool_workspace_path,
        session_id=request.session_id,
        abort=abort_event,
        extras=request.tool_context_extras,
    )

    tool_summaries = [(t.name, t.description) for t in (deps.tools.get(n) for n in deps.tools.names()) if t is not None]
    system_prompt = await build_system_prompt(
        PromptInputs(
            session_id=request.session_id,
            workspace_id=request.workspace_id,
            agent_id=request.agent_id,
            model=request.model or deps.llm.default_model,
            tools=tool_summaries,
            workspace_path=str(tool_workspace_path),
        ),
        hooks=deps.hooks,
        user_prompt=request.user_message,
    )

    iteration = 0
    total_input_tokens = 0
    total_output_tokens = 0
    final_text = ""

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

        try:
            response: LLMResponse = await _call_llm_with_limits(
                deps,
                messages=assembled.messages,
                model=request.model,
                tools=deps.tools.list_for_llm() or None,
                system=effective_system,
                abort_event=abort_event,
                remaining_run_s=remaining_run,
            )
        except AgentTimeoutError as te:
            yield ErrorEvent(error_code="timeout", message=str(te))
            return
        except AgentAbortedError:
            yield ErrorEvent(error_code="aborted", message="run aborted during llm call")
            return
        except LLMError as exc:
            if exc.code == "context_overflow":
                compact_result = await deps.context_engine.compact(
                    session_id=request.session_id,
                    messages=base_messages,
                    token_budget=deps.config.context_window,
                    force=True,
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
                    await deps.session_store.append_entry(
                        tree.header.id, comp_entry, leaf_id=comp_entry.id
                    )
                    continue
            yield ErrorEvent(error_code=exc.code, message=str(exc))
            return

        total_input_tokens += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        if response.text:
            yield TextChunk(text=response.text)
            final_text = response.text

        if not response.tool_calls:
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

        for call in response.tool_calls:
            fn = (call or {}).get("function") or {}
            yield ToolCallStart(
                tool_call_id=call.get("id", ""),
                name=fn.get("name", "") or "",
                arguments=fn.get("arguments") or {},
            )

        results: list[ToolResult] = await execute_tool_calls(
            deps.tools,
            response.tool_calls,
            tool_ctx,
            default_tool_timeout_s=deps.config.timeouts.tool_seconds,
        )

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

    yield ErrorEvent(
        error_code="max_iterations",
        message=f"reached max_iterations={deps.config.max_iterations}",
    )


def _remaining_run_seconds(run_deadline_s: float, run_started: float) -> float | None:
    if run_deadline_s <= 0:
        return None
    remaining = run_deadline_s - (time.monotonic() - run_started)
    return remaining


async def _call_llm_with_limits(
    deps: AgentRunnerDeps,
    *,
    messages: list[dict[str, Any]],
    model: str | None,
    tools: list[dict[str, Any]] | None,
    system: str | None,
    abort_event: asyncio.Event,
    remaining_run_s: float | None,
) -> LLMResponse:
    from pyclaw.core.agent.runtime_util import run_with_timeout

    idle_s = deps.config.timeouts.idle_seconds

    async def _call() -> LLMResponse:
        return await deps.llm.complete(
            messages=messages,
            model=model,
            tools=tools,
            system=system,
            idle_seconds=idle_s,
            abort_event=abort_event,
        )

    timeout_for_wait = remaining_run_s if remaining_run_s is not None else 0.0
    if timeout_for_wait < 0:
        timeout_for_wait = 0.0
    return await run_with_timeout(
        _call(),
        timeout_s=timeout_for_wait,
        abort_event=abort_event,
        kind="run",
    )
