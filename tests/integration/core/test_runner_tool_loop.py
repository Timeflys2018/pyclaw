from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolContext, ToolRegistry, error_result, text_result
from pyclaw.models import (
    AgentRunConfig,
    Done,
    ErrorEvent,
    RetryConfig,
    ToolResult,
)


class _ScriptedLLM(LLMClient):
    def __init__(self, script: list[list[LLMStreamChunk]]) -> None:
        super().__init__(default_model="fake")
        self._script = list(script)
        self.call_count = 0

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
        if not self._script:
            raise AssertionError("Scripted LLM exhausted")
        self.call_count += 1
        for c in self._script.pop(0):
            yield c


def _tool_call_turn(name: str, call_id: str) -> list[LLMStreamChunk]:
    import json

    return [
        LLMStreamChunk(
            tool_call_deltas=[
                {
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps({"_call_id": call_id}),
                    },
                }
            ]
        ),
        LLMStreamChunk(finish_reason="tool_calls", usage=LLMUsage()),
    ]


def _text_turn(text: str) -> list[LLMStreamChunk]:
    return [
        LLMStreamChunk(text_delta=text),
        LLMStreamChunk(finish_reason="stop", usage=LLMUsage()),
    ]


class _RealTool:
    name = "echo"
    description = "echo"
    parameters: dict = {"type": "object", "properties": {}}
    side_effect = False

    async def execute(self, args: dict, context: ToolContext) -> ToolResult:
        return text_result(args.get("_call_id", ""), "ok")


class _FailingTool:
    name = "bash_fail"
    description = "bash fail"
    parameters: dict = {"type": "object", "properties": {}}
    side_effect = True

    async def execute(self, args: dict, context: ToolContext) -> ToolResult:
        return error_result(args.get("_call_id", ""), "exit 1")


@pytest.mark.asyncio
async def test_unknown_tool_threshold_terminates_with_tool_loop(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            _tool_call_turn("fake_tool", "c1"),
            _tool_call_turn("fake_tool", "c2"),
            _tool_call_turn("fake_tool", "c3"),
            _tool_call_turn("fake_tool", "c4"),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=0,
                empty_response_limit=0,
                unknown_tool_threshold=3,
            )
        ),
    )
    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s", workspace_id="default", agent_id="main", user_message="go"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert errors
    assert errors[-1].error_code == "tool_loop"


@pytest.mark.asyncio
async def test_unknown_tool_counter_resets_on_different_name(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(_RealTool())
    llm = _ScriptedLLM(
        [
            _tool_call_turn("fake_a", "c1"),
            _tool_call_turn("echo", "c2"),
            _tool_call_turn("fake_a", "c3"),
            _text_turn("done"),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=registry,
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=0,
                empty_response_limit=0,
                unknown_tool_threshold=3,
            ),
            max_iterations=10,
        ),
    )
    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s2", workspace_id="default", agent_id="main", user_message="go"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "done"


@pytest.mark.asyncio
async def test_known_tool_failures_not_counted(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(_FailingTool())
    llm = _ScriptedLLM(
        [
            _tool_call_turn("bash_fail", "c1"),
            _tool_call_turn("bash_fail", "c2"),
            _tool_call_turn("bash_fail", "c3"),
            _tool_call_turn("bash_fail", "c4"),
            _text_turn("giving up"),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=registry,
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=0,
                empty_response_limit=0,
                unknown_tool_threshold=2,
            ),
            max_iterations=10,
        ),
    )
    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s3", workspace_id="default", agent_id="main", user_message="go"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "giving up"


@pytest.mark.asyncio
async def test_unknown_tool_threshold_disabled_when_zero(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            _tool_call_turn("fake_tool", "c1"),
            _tool_call_turn("fake_tool", "c2"),
            _tool_call_turn("fake_tool", "c3"),
            _text_turn("done"),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=0,
                empty_response_limit=0,
                unknown_tool_threshold=0,
            ),
            max_iterations=10,
        ),
    )
    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s4", workspace_id="default", agent_id="main", user_message="go"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors


@pytest.mark.asyncio
async def test_guidance_injected_at_threshold_before_termination(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            _tool_call_turn("fake_tool", "c1"),
            _tool_call_turn("fake_tool", "c2"),
            _tool_call_turn("fake_tool", "c3"),
            _text_turn("okay, giving up on fake_tool"),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=0,
                empty_response_limit=0,
                unknown_tool_threshold=3,
            ),
            max_iterations=10,
        ),
    )
    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s5", workspace_id="default", agent_id="main", user_message="go"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "okay, giving up on fake_tool"
