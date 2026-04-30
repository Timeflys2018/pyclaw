from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMResponse, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.models import AgentRunConfig, ErrorEvent, TimeoutConfig


class _SlowLLM(LLMClient):
    async def stream(  # type: ignore[override]
        self,
        *,
        messages,
        model=None,
        tools=None,
        system=None,
        idle_seconds: float = 0.0,
        abort_event=None,
    ):
        await asyncio.sleep(5)
        for chunk in ():
            yield chunk

    async def complete(  # type: ignore[override]
        self,
        *,
        messages,
        model=None,
        tools=None,
        system=None,
        idle_seconds: float = 0.0,
        abort_event=None,
    ) -> LLMResponse:
        await asyncio.sleep(5)
        return LLMResponse(
            text="late",
            tool_calls=[],
            usage=LLMUsage(),
            finish_reason="stop",
        )


@pytest.mark.asyncio
async def test_run_timeout_triggers_error_event(tmp_path: Path) -> None:
    llm = _SlowLLM(default_model="fake")
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(timeouts=TimeoutConfig(run_seconds=0.05, idle_seconds=0.0)),
    )
    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(
            session_id="t1",
            workspace_id="default",
            agent_id="main",
            user_message="hi",
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors, f"expected an ErrorEvent, got {events!r}"
    assert errors[-1].error_code == "timeout"


@pytest.mark.asyncio
async def test_abort_triggers_aborted_error_event(tmp_path: Path) -> None:
    llm = _SlowLLM(default_model="fake")
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(timeouts=TimeoutConfig(run_seconds=10.0, idle_seconds=0.0)),
    )
    abort = asyncio.Event()

    async def _signal() -> None:
        await asyncio.sleep(0.02)
        abort.set()

    asyncio.create_task(_signal())

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(
            session_id="t2",
            workspace_id="default",
            agent_id="main",
            user_message="hi",
        ),
        deps,
        tool_workspace_path=tmp_path,
        abort=abort,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors
    assert errors[-1].error_code == "aborted"
