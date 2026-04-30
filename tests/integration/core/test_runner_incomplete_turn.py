from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolContext, ToolRegistry, text_result
from pyclaw.models import (
    AgentRunConfig,
    Done,
    MessageEntry,
    RetryConfig,
    TextChunk,
    ToolResult,
)


class _ScriptedLLM(LLMClient):
    def __init__(self, script: list[list[LLMStreamChunk]]) -> None:
        super().__init__(default_model="fake")
        self._script = list(script)
        self.call_count = 0
        self.last_messages: list[list[dict[str, Any]]] = []

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
        self.last_messages.append(list(messages))
        if not self._script:
            raise AssertionError("Scripted LLM exhausted")
        chunks = self._script.pop(0)
        self.call_count += 1
        for c in chunks:
            yield c


def _text_turn(text: str) -> list[LLMStreamChunk]:
    return [
        LLMStreamChunk(text_delta=text),
        LLMStreamChunk(finish_reason="stop", usage=LLMUsage()),
    ]


@pytest.mark.asyncio
async def test_planning_retry_then_success(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            _text_turn("I'll read the config and update it."),
            _text_turn("The config says foo=1."),
        ]
    )
    deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry())

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s", workspace_id="default", agent_id="main", user_message="hi"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    assert llm.call_count == 2
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "The config says foo=1."


@pytest.mark.asyncio
async def test_planning_retry_exhausted_terminates(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            _text_turn("I'll update it."),
            _text_turn("Let me update it."),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=1,
                reasoning_only_limit=0,
                empty_response_limit=0,
            )
        ),
    )

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s2", workspace_id="default", agent_id="main", user_message="hi"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    assert llm.call_count == 2
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "Let me update it."


@pytest.mark.asyncio
async def test_planning_detection_disabled_when_limit_zero(tmp_path: Path) -> None:
    llm = _ScriptedLLM([_text_turn("I'll do that for you.")])
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=0,
                empty_response_limit=0,
            )
        ),
    )

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s3", workspace_id="default", agent_id="main", user_message="hi"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    assert llm.call_count == 1
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "I'll do that for you."


@pytest.mark.asyncio
async def test_empty_response_triggers_retry(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            [LLMStreamChunk(finish_reason="stop", usage=LLMUsage())],
            _text_turn("Here is the answer."),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=0,
                empty_response_limit=1,
            )
        ),
    )

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s4", workspace_id="default", agent_id="main", user_message="hi"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    assert llm.call_count == 2
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "Here is the answer."


@pytest.mark.asyncio
async def test_reasoning_only_triggers_retry(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            _text_turn("<thinking>computing...</thinking>"),
            _text_turn("The answer is 42."),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=2,
                empty_response_limit=0,
            )
        ),
    )

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s5", workspace_id="default", agent_id="main", user_message="hi"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    assert llm.call_count == 2
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "The answer is 42."


class _EchoTool:
    name = "echo"
    description = "echo"
    parameters: dict = {"type": "object", "properties": {}}
    side_effect = False

    async def execute(self, args: dict, context: ToolContext) -> ToolResult:
        return text_result(args.get("_call_id", ""), "ok")


def _tool_call_turn(call_id: str = "c1") -> list[LLMStreamChunk]:
    return [
        LLMStreamChunk(
            tool_call_deltas=[
                {
                    "index": 0,
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": json.dumps({"_call_id": call_id}),
                    },
                }
            ]
        ),
        LLMStreamChunk(finish_reason="tool_calls", usage=LLMUsage()),
    ]


@pytest.mark.asyncio
async def test_retry_counts_reset_after_tool_round(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            _text_turn("I'll handle it."),
            _tool_call_turn("c1"),
            _text_turn("I'll check one more thing."),
            _text_turn("Done."),
        ]
    )
    registry = ToolRegistry()
    registry.register(_EchoTool())
    deps = AgentRunnerDeps(
        llm=llm,
        tools=registry,
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=1,
                reasoning_only_limit=0,
                empty_response_limit=0,
                unknown_tool_threshold=0,
            ),
            max_iterations=10,
        ),
    )

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="r1", workspace_id="default", agent_id="main", user_message="go"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    assert llm.call_count == 4
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "Done."


@pytest.mark.asyncio
async def test_retry_counts_independent_per_category(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            _text_turn("I'll do that."),
            _tool_call_turn("c2"),
            _text_turn("<thinking>computing</thinking>"),
            _text_turn("<thinking>still computing</thinking>"),
            _text_turn("Final answer."),
        ]
    )
    registry = ToolRegistry()
    registry.register(_EchoTool())
    deps = AgentRunnerDeps(
        llm=llm,
        tools=registry,
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=1,
                reasoning_only_limit=2,
                empty_response_limit=0,
                unknown_tool_threshold=0,
            ),
            max_iterations=10,
        ),
    )

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="r2", workspace_id="default", agent_id="main", user_message="go"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    assert llm.call_count == 5
    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "Final answer."


@pytest.mark.asyncio
async def test_done_final_message_excludes_planning_text(tmp_path: Path) -> None:
    planning_text = "I'll read the config and update it."
    final_answer = "Result: 42"

    registry = ToolRegistry()
    registry.register(_EchoTool())
    llm = _ScriptedLLM(
        [
            _text_turn(planning_text),
            _tool_call_turn("c3"),
            _text_turn(final_answer),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=registry,
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=1,
                reasoning_only_limit=0,
                empty_response_limit=0,
                unknown_tool_threshold=0,
            ),
            max_iterations=10,
        ),
    )

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="fm1", workspace_id="default", agent_id="main", user_message="hi"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == final_answer, (
        f"expected {final_answer!r}, got {done.final_message!r}"
    )


@pytest.mark.asyncio
async def test_done_final_message_after_reasoning_retry(tmp_path: Path) -> None:
    llm = _ScriptedLLM(
        [
            _text_turn("<thinking>computing...</thinking>"),
            _text_turn("The answer is foo."),
        ]
    )
    deps = AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        config=AgentRunConfig(
            retry=RetryConfig(
                planning_only_limit=0,
                reasoning_only_limit=2,
                empty_response_limit=0,
                unknown_tool_threshold=0,
            ),
        ),
    )

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="fm2", workspace_id="default", agent_id="main", user_message="hi"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "The answer is foo."
