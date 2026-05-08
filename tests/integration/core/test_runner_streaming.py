from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolContext, ToolRegistry, text_result
from pyclaw.models import Done, TextChunk, ToolCallEnd, ToolCallStart, ToolResult


class _StreamingLLM(LLMClient):
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
            raise AssertionError("StreamingLLM exhausted")
        chunks = self._script.pop(0)
        self.call_count += 1
        for c in chunks:
            await asyncio.sleep(0)
            yield c


class _EchoTool:
    name = "echo"
    description = "Echo"
    parameters: dict = {"type": "object", "properties": {"text": {"type": "string"}}}
    side_effect = False

    async def execute(self, args: dict, context: ToolContext) -> ToolResult:
        return text_result(args.get("_call_id", ""), f"echo:{args.get('text', '')}")


@pytest.mark.asyncio
async def test_text_deltas_yielded_incrementally(tmp_path: Path) -> None:
    llm = _StreamingLLM(
        [
            [
                LLMStreamChunk(text_delta="Hel"),
                LLMStreamChunk(text_delta="lo "),
                LLMStreamChunk(text_delta="world"),
                LLMStreamChunk(finish_reason="stop", usage=LLMUsage(input_tokens=5, output_tokens=3)),
            ]
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

    text_chunks = [e for e in events if isinstance(e, TextChunk)]
    assert [c.text for c in text_chunks] == ["Hel", "lo ", "world"]

    done = next(e for e in events if isinstance(e, Done))
    assert done.final_message == "Hello world"
    assert done.usage.get("input") == 5
    assert done.usage.get("output") == 3


@pytest.mark.asyncio
async def test_tool_call_deltas_reassembled(tmp_path: Path) -> None:
    llm = _StreamingLLM(
        [
            [
                LLMStreamChunk(text_delta="calling "),
                LLMStreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "echo", "arguments": '{"text":'},
                        }
                    ]
                ),
                LLMStreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": None,
                            "type": "function",
                            "function": {"name": "", "arguments": '"hi","_call_id":"call_1"}'},
                        }
                    ]
                ),
                LLMStreamChunk(finish_reason="tool_calls", usage=LLMUsage()),
            ],
            [
                LLMStreamChunk(text_delta="done"),
                LLMStreamChunk(finish_reason="stop", usage=LLMUsage()),
            ],
        ]
    )
    registry = ToolRegistry()
    registry.register(_EchoTool())
    deps = AgentRunnerDeps(llm=llm, tools=registry)

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s2", workspace_id="default", agent_id="main", user_message="call"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    starts = [e for e in events if isinstance(e, ToolCallStart)]
    assert len(starts) == 1
    assert starts[0].name == "echo"
    assert starts[0].arguments.get("text") == "hi"

    ends = [e for e in events if isinstance(e, ToolCallEnd)]
    assert len(ends) == 1
    assert "echo:hi" in ends[0].result.content[0].text

    text_chunks = [e for e in events if isinstance(e, TextChunk)]
    assert any(c.text == "calling " for c in text_chunks)
    assert any(c.text == "done" for c in text_chunks)


@pytest.mark.asyncio
async def test_usage_propagates_to_done_event(tmp_path: Path) -> None:
    llm = _StreamingLLM(
        [
            [
                LLMStreamChunk(text_delta="ok"),
                LLMStreamChunk(
                    finish_reason="stop",
                    usage=LLMUsage(input_tokens=42, output_tokens=7, total_tokens=49),
                ),
            ]
        ]
    )
    deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry())

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s3", workspace_id="default", agent_id="main", user_message="hi"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    done = next(e for e in events if isinstance(e, Done))
    assert done.usage["input"] == 42
    assert done.usage["output"] == 7
    assert done.usage["cache_creation"] == 0
    assert done.usage["cache_read"] == 0


@pytest.mark.asyncio
async def test_tool_call_emitted_after_text_chunks(tmp_path: Path) -> None:
    llm = _StreamingLLM(
        [
            [
                LLMStreamChunk(text_delta="thinking..."),
                LLMStreamChunk(
                    tool_call_deltas=[
                        {
                            "index": 0,
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "echo", "arguments": '{"text":"x","_call_id":"c1"}'},
                        }
                    ]
                ),
                LLMStreamChunk(finish_reason="tool_calls"),
            ],
            [
                LLMStreamChunk(text_delta="final"),
                LLMStreamChunk(finish_reason="stop"),
            ],
        ]
    )
    registry = ToolRegistry()
    registry.register(_EchoTool())
    deps = AgentRunnerDeps(llm=llm, tools=registry)

    events: list[Any] = []
    async for event in run_agent_stream(
        RunRequest(session_id="s4", workspace_id="default", agent_id="main", user_message="go"),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(event)

    first_text_idx = next(i for i, e in enumerate(events) if isinstance(e, TextChunk))
    first_start_idx = next(i for i, e in enumerate(events) if isinstance(e, ToolCallStart))
    assert first_text_idx < first_start_idx
