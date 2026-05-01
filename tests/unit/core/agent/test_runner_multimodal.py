from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.hooks import HookRegistry
from pyclaw.models import AgentRunConfig, Done, ImageBlock


class _CaptureLLM(LLMClient):
    def __init__(self) -> None:
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
        yield LLMStreamChunk(text_delta="ok")
        yield LLMStreamChunk(finish_reason="stop", usage=LLMUsage())


def _make_deps(llm: LLMClient) -> AgentRunnerDeps:
    return AgentRunnerDeps(
        llm=llm,
        tools=ToolRegistry(),
        hooks=HookRegistry(),
        config=AgentRunConfig(),
    )


@pytest.mark.asyncio
async def test_image_attachment_prepended_to_user_message() -> None:
    llm = _CaptureLLM()
    deps = _make_deps(llm)
    img = ImageBlock(type="image", data="b64data", mime_type="image/jpeg")
    request = RunRequest(
        session_id="mm-1",
        workspace_id="ws",
        agent_id="default",
        user_message="describe",
        attachments=[img],
    )

    events = []
    async for event in run_agent_stream(request, deps, tool_workspace_path=Path(".")):
        events.append(event)

    assert any(isinstance(e, Done) for e in events)

    user_msg = next(m for m in llm.last_messages if m["role"] == "user")
    content = user_msg["content"]
    assert isinstance(content, list)
    assert any(block.get("type") == "image_url" for block in content)
    assert any(block.get("type") == "text" for block in content)


@pytest.mark.asyncio
async def test_no_attachment_uses_plain_text() -> None:
    llm = _CaptureLLM()
    deps = _make_deps(llm)
    request = RunRequest(
        session_id="mm-2",
        workspace_id="ws",
        agent_id="default",
        user_message="hello",
        attachments=[],
    )

    events = []
    async for event in run_agent_stream(request, deps, tool_workspace_path=Path(".")):
        events.append(event)

    assert any(isinstance(e, Done) for e in events)
    user_msg = next(m for m in llm.last_messages if m["role"] == "user")
    assert user_msg["content"] == "hello"
