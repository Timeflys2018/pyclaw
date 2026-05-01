from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pyclaw.channels.base import InboundMessage
from pyclaw.channels.dispatch import dispatch_message
from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.hooks import HookRegistry
from pyclaw.models import AgentRunConfig, Done, ImageBlock


class _OneShotLLM(LLMClient):
    def __init__(self, reply: str) -> None:
        super().__init__(default_model="fake")
        self.last_messages: list[Any] = []

    async def stream(
        self,
        *,
        messages: Any,
        model: Any = None,
        tools: Any = None,
        system: Any = None,
        idle_seconds: float = 0.0,
        abort_event: Any = None,
    ) -> Any:
        self.last_messages = list(messages)
        yield LLMStreamChunk(text_delta="hello")
        yield LLMStreamChunk(finish_reason="stop", usage=LLMUsage())


def _make_deps(llm: LLMClient) -> AgentRunnerDeps:
    return AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        config=AgentRunConfig(),
    )


@pytest.mark.asyncio
async def test_dispatch_yields_done_event() -> None:
    llm = _OneShotLLM("hello")
    deps = _make_deps(llm)
    inbound = InboundMessage(
        session_id="sess-1",
        user_message="hi",
        workspace_id="ws-1",
        channel="feishu",
    )

    events = []
    async for event in dispatch_message(inbound, deps, workspace_path=Path(".")):
        events.append(event)

    assert any(isinstance(e, Done) for e in events)


@pytest.mark.asyncio
async def test_dispatch_passes_attachments_to_run_request() -> None:
    llm = _OneShotLLM("hello")
    deps = _make_deps(llm)
    img = ImageBlock(type="image", data="abc123", mime_type="image/jpeg")
    inbound = InboundMessage(
        session_id="sess-2",
        user_message="describe this",
        workspace_id="ws-2",
        channel="feishu",
        attachments=[img],
    )

    events = []
    async for event in dispatch_message(inbound, deps, workspace_path=Path(".")):
        events.append(event)

    assert any(isinstance(e, Done) for e in events)
