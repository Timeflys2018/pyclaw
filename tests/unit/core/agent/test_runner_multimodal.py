from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.hooks import HookRegistry
from pyclaw.infra.settings import ModelEntry, ModelModalities, ProviderSettings
from pyclaw.models import AgentRunConfig, Done, ErrorEvent, ImageBlock


class _CaptureLLM(LLMClient):
    def __init__(
        self,
        *,
        default_model: str = "fake",
        providers: dict[str, ProviderSettings] | None = None,
    ) -> None:
        super().__init__(default_model=default_model, providers=providers)
        self.last_messages: list[Any] = []
        self.stream_call_count = 0

    async def stream(
        self,
        *,
        messages: Any,
        model: Any = None,
        tools: Any = None,
        system: Any = None,
        idle_seconds: float = 0.0,
        abort_event: Any = None,
        temperature: float | None = None,
    ) -> Any:
        self.stream_call_count += 1
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


def _vision_text_providers() -> dict[str, ProviderSettings]:
    return {
        "openai": ProviderSettings(
            api_key="ok",
            base_url="ob",
            prefixes=["azure_openai"],
            models={
                "azure_openai/gpt-5.4": ModelEntry(
                    modalities=ModelModalities(input={"text", "image"}, output={"text"})
                ),
                "azure_openai/gpt-5.3-codex": ModelEntry(
                    modalities=ModelModalities(input={"text"}, output={"text"})
                ),
            },
        ),
    }


@pytest.mark.asyncio
async def test_image_plus_text_appends_both_blocks() -> None:
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
async def test_image_only_appends_only_imageblock_no_empty_textblock() -> None:
    llm = _CaptureLLM()
    deps = _make_deps(llm)
    img = ImageBlock(type="image", data="b64data", mime_type="image/jpeg")
    request = RunRequest(
        session_id="mm-image-only",
        workspace_id="ws",
        agent_id="default",
        user_message="",
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
    assert all(block.get("type") != "text" for block in content), (
        f"empty user_message MUST NOT produce a TextBlock; got {content}"
    )


@pytest.mark.asyncio
async def test_image_plus_whitespace_only_text_treats_as_image_only() -> None:
    llm = _CaptureLLM()
    deps = _make_deps(llm)
    img = ImageBlock(type="image", data="b64data", mime_type="image/jpeg")
    request = RunRequest(
        session_id="mm-ws-only",
        workspace_id="ws",
        agent_id="default",
        user_message="   \t\n  ",
        attachments=[img],
    )

    events = []
    async for event in run_agent_stream(request, deps, tool_workspace_path=Path(".")):
        events.append(event)

    user_msg = next(m for m in llm.last_messages if m["role"] == "user")
    content = user_msg["content"]
    assert isinstance(content, list)
    assert all(block.get("type") != "text" for block in content)


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


class TestRunnerVisionPreflight:
    @pytest.mark.asyncio
    async def test_primary_rejects_image_with_non_vision_model_no_persistence(self) -> None:
        llm = _CaptureLLM(
            default_model="azure_openai/gpt-5.3-codex",
            providers=_vision_text_providers(),
        )
        deps = _make_deps(llm)
        img = ImageBlock(type="image", data="b64", mime_type="image/jpeg")
        request = RunRequest(
            session_id="vision-rej-1",
            workspace_id="ws",
            agent_id="default",
            user_message="what is this",
            attachments=[img],
            model="azure_openai/gpt-5.3-codex",
        )

        events = []
        async for event in run_agent_stream(request, deps, tool_workspace_path=Path(".")):
            events.append(event)

        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert error_events, f"expected ErrorEvent; got {events}"
        assert error_events[0].error_code == "vision_not_support"

        tree = await deps.session_store.load("vision-rej-1")
        if tree is not None:
            user_entries = [e for e in tree.entries.values() if getattr(e, "role", None) == "user"]
            assert not user_entries, (
                f"PRIMARY check failed-path MUST NOT persist user_entry; got {user_entries}"
            )

    @pytest.mark.asyncio
    async def test_primary_allows_image_with_vision_model_normal_flow(self) -> None:
        llm = _CaptureLLM(
            default_model="azure_openai/gpt-5.4",
            providers=_vision_text_providers(),
        )
        deps = _make_deps(llm)
        img = ImageBlock(type="image", data="b64", mime_type="image/jpeg")
        request = RunRequest(
            session_id="vision-ok-1",
            workspace_id="ws",
            agent_id="default",
            user_message="what is this",
            attachments=[img],
            model="azure_openai/gpt-5.4",
        )

        events = []
        async for event in run_agent_stream(request, deps, tool_workspace_path=Path(".")):
            events.append(event)

        assert any(isinstance(e, Done) for e in events)
        assert llm.stream_call_count == 1

    @pytest.mark.asyncio
    async def test_primary_skips_when_no_attachments(self) -> None:
        llm = _CaptureLLM(
            default_model="azure_openai/gpt-5.3-codex",
            providers=_vision_text_providers(),
        )
        deps = _make_deps(llm)
        request = RunRequest(
            session_id="text-only-1",
            workspace_id="ws",
            agent_id="default",
            user_message="hello",
            attachments=[],
            model="azure_openai/gpt-5.3-codex",
        )

        events = []
        async for event in run_agent_stream(request, deps, tool_workspace_path=Path(".")):
            events.append(event)

        assert any(isinstance(e, Done) for e in events)
        assert llm.stream_call_count == 1

    @pytest.mark.asyncio
    async def test_primary_skips_when_providers_empty_legacy(self) -> None:
        llm = _CaptureLLM()
        deps = _make_deps(llm)
        img = ImageBlock(type="image", data="b64", mime_type="image/jpeg")
        request = RunRequest(
            session_id="legacy-1",
            workspace_id="ws",
            agent_id="default",
            user_message="x",
            attachments=[img],
        )

        events = []
        async for event in run_agent_stream(request, deps, tool_workspace_path=Path(".")):
            events.append(event)

        assert any(isinstance(e, Done) for e in events)

    @pytest.mark.asyncio
    async def test_primary_yields_error_event_with_vision_not_support_code(self) -> None:
        llm = _CaptureLLM(
            default_model="azure_openai/gpt-5.3-codex",
            providers=_vision_text_providers(),
        )
        deps = _make_deps(llm)
        img = ImageBlock(type="image", data="b64", mime_type="image/jpeg")
        request = RunRequest(
            session_id="vision-rej-2",
            workspace_id="ws",
            agent_id="default",
            user_message="what is this",
            attachments=[img],
            model="azure_openai/gpt-5.3-codex",
        )

        events = []
        async for event in run_agent_stream(request, deps, tool_workspace_path=Path(".")):
            events.append(event)

        first_error = next((e for e in events if isinstance(e, ErrorEvent)), None)
        assert first_error is not None
        assert first_error.error_code == "vision_not_support"
        assert "azure_openai/gpt-5.3-codex" in first_error.message
        assert "azure_openai/gpt-5.4" in first_error.message

    @pytest.mark.asyncio
    async def test_primary_does_not_call_llm_stream(self) -> None:
        llm = _CaptureLLM(
            default_model="azure_openai/gpt-5.3-codex",
            providers=_vision_text_providers(),
        )
        deps = _make_deps(llm)
        img = ImageBlock(type="image", data="b64", mime_type="image/jpeg")
        request = RunRequest(
            session_id="vision-rej-3",
            workspace_id="ws",
            agent_id="default",
            user_message="x",
            attachments=[img],
            model="azure_openai/gpt-5.3-codex",
        )

        events = []
        async for event in run_agent_stream(request, deps, tool_workspace_path=Path(".")):
            events.append(event)

        assert llm.stream_call_count == 0
