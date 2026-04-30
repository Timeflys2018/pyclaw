from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMResponse, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolContext, ToolRegistry, text_result
from pyclaw.models import Done, TextChunk, ToolCallEnd, ToolCallStart, ToolResult


class _FakeLLM(LLMClient):
    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(default_model="fake-model")
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, messages, model=None, tools=None, system=None) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        if not self._responses:
            raise AssertionError("FakeLLM exhausted")
        return self._responses.pop(0)


class _EchoTool:
    name = "echo"
    description = "Echo the provided text"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    side_effect = False

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        return text_result(args.get("_call_id", "x"), f"echo:{args.get('text', '')}")


def _usage(in_tokens: int = 10, out_tokens: int = 5) -> LLMUsage:
    return LLMUsage(input_tokens=in_tokens, output_tokens=out_tokens, total_tokens=in_tokens + out_tokens)


class TestAgentLoop:
    async def test_simple_text_response(self, tmp_path: Path) -> None:
        llm = _FakeLLM([LLMResponse(text="hello world", tool_calls=[], usage=_usage(), finish_reason="stop")])
        registry = ToolRegistry()
        deps = AgentRunnerDeps(llm=llm, tools=registry)

        events = []
        async for event in run_agent_stream(
            RunRequest(
                session_id="s1",
                workspace_id="default",
                agent_id="main",
                user_message="hi",
            ),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        assert any(isinstance(e, TextChunk) and e.text == "hello world" for e in events)
        assert any(isinstance(e, Done) for e in events)
        assert len(llm.calls) == 1

    async def test_tool_call_loop(self, tmp_path: Path) -> None:
        llm = _FakeLLM(
            [
                LLMResponse(
                    text="",
                    tool_calls=[
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "echo", "arguments": {"text": "hi", "_call_id": "call_1"}},
                        }
                    ],
                    usage=_usage(),
                    finish_reason="tool_calls",
                ),
                LLMResponse(text="done", tool_calls=[], usage=_usage(), finish_reason="stop"),
            ]
        )
        registry = ToolRegistry()
        registry.register(_EchoTool())
        deps = AgentRunnerDeps(llm=llm, tools=registry)

        events = []
        async for event in run_agent_stream(
            RunRequest(
                session_id="s2",
                workspace_id="default",
                agent_id="main",
                user_message="call echo",
            ),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        starts = [e for e in events if isinstance(e, ToolCallStart)]
        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(starts) == 1
        assert starts[0].name == "echo"
        assert len(ends) == 1
        assert "echo:hi" in ends[0].result.content[0].text
        assert any(isinstance(e, Done) and e.final_message == "done" for e in events)
        assert len(llm.calls) == 2

    async def test_max_iterations_terminates(self, tmp_path: Path) -> None:
        def infinite_tool_response() -> LLMResponse:
            return LLMResponse(
                text="",
                tool_calls=[
                    {
                        "id": "call_x",
                        "type": "function",
                        "function": {"name": "echo", "arguments": {"text": "loop", "_call_id": "call_x"}},
                    }
                ],
                usage=_usage(),
                finish_reason="tool_calls",
            )

        llm = _FakeLLM([infinite_tool_response() for _ in range(10)])
        registry = ToolRegistry()
        registry.register(_EchoTool())

        from pyclaw.models import AgentRunConfig

        deps = AgentRunnerDeps(
            llm=llm,
            tools=registry,
            config=AgentRunConfig(max_iterations=3, context_window=100_000),
        )

        events = []
        async for event in run_agent_stream(
            RunRequest(
                session_id="s3",
                workspace_id="default",
                agent_id="main",
                user_message="loop",
            ),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        from pyclaw.models import ErrorEvent

        errors = [e for e in events if isinstance(e, ErrorEvent)]
        assert errors
        assert errors[0].error_code == "max_iterations"
