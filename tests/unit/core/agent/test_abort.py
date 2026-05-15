from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMError, LLMStreamChunk
from pyclaw.core.agent.tools.builtin import BashTool
from pyclaw.core.agent.tools.registry import ToolContext, wrap_tool_with_abort
from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.models import TextBlock, ToolResult


def _ctx(abort: asyncio.Event) -> ToolContext:
    return ToolContext(
        workspace_id="default",
        workspace_path=Path("."),
        session_id="abort-test",
        abort=abort,
    )


@pytest.mark.asyncio
async def test_bash_aborts_before_spawn() -> None:
    abort = asyncio.Event()
    abort.set()
    result = await BashTool().execute({"command": "echo hi", "_call_id": "c1"}, _ctx(abort))
    assert result.is_error
    assert "aborted before spawn" in result.content[0].text


@pytest.mark.asyncio
async def test_bash_aborts_running_subprocess() -> None:
    abort = asyncio.Event()
    ctx = _ctx(abort)

    async def _signal() -> None:
        await asyncio.sleep(0.05)
        abort.set()

    asyncio.create_task(_signal())
    result = await BashTool().execute({"command": "sleep 10", "_call_id": "c1"}, ctx)
    assert result.is_error
    assert "aborted" in result.content[0].text


@pytest.mark.asyncio
async def test_wrap_tool_with_abort_blocks_pre_set() -> None:
    class _Fast:
        name = "fast"
        description = "fast tool"
        parameters: dict = {"type": "object", "properties": {}}
        side_effect = False

        async def execute(self, args: dict, context: ToolContext) -> ToolResult:
            return ToolResult(
                tool_call_id=args.get("_call_id", ""),
                content=[TextBlock(text="ran")],
                is_error=False,
            )

    wrapped = wrap_tool_with_abort(_Fast())
    abort = asyncio.Event()
    abort.set()
    result = await wrapped.execute({"_call_id": "c1"}, _ctx(abort))
    assert result.is_error
    assert "aborted" in result.content[0].text


class _StuckLLM(LLMClient):
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
        from pyclaw.core.agent.runtime_util import iterate_with_idle_timeout

        async def _hang():
            yield LLMStreamChunk(text_delta="hello")
            await asyncio.sleep(10)
            yield LLMStreamChunk(text_delta="never")

        try:
            async for chunk in iterate_with_idle_timeout(
                _hang(),
                idle_seconds=idle_seconds,
                abort_event=abort_event,
                kind="idle",
            ):
                yield chunk
        except Exception as exc:
            raise LLMError("aborted", str(exc)) from exc


@pytest.mark.asyncio
async def test_llm_stream_aborts_mid_stream() -> None:
    abort = asyncio.Event()
    client = _StuckLLM(default_model="fake")

    async def _signal() -> None:
        await asyncio.sleep(0.05)
        abort.set()

    asyncio.create_task(_signal())

    chunks = []
    with pytest.raises(LLMError) as info:
        async for chunk in client.stream(
            messages=[{"role": "user", "content": "hi"}],
            idle_seconds=0.0,
            abort_event=abort,
        ):
            chunks.append(chunk)

    assert chunks and chunks[0].text_delta == "hello"
    assert info.value.code == "aborted"


@pytest.mark.asyncio
async def test_context_engine_compact_aborts_during_summary() -> None:
    abort = asyncio.Event()
    call_started = asyncio.Event()

    async def _hanging_summarizer(_payload) -> str:
        call_started.set()
        await asyncio.sleep(10)
        return "unreachable"

    engine = DefaultContextEngine(summarize=_hanging_summarizer, keep_recent_tokens=100)

    msgs = [
        {"role": "user", "content": "x" * 4000},
        {"role": "assistant", "content": "y" * 4000},
    ] * 10

    async def _signal() -> None:
        await call_started.wait()
        abort.set()

    asyncio.create_task(_signal())

    result = await engine.compact(
        session_id="s1",
        messages=msgs,
        token_budget=100,
        force=True,
        abort_event=abort,
    )
    assert result.ok is False
    assert result.reason_code == "aborted"


@pytest.mark.asyncio
async def test_context_engine_compact_aborts_when_preset() -> None:
    engine = DefaultContextEngine()
    abort = asyncio.Event()
    abort.set()

    result = await engine.compact(
        session_id="s1",
        messages=[{"role": "user", "content": "hi"}],
        token_budget=1000,
        abort_event=abort,
    )
    assert result.ok is False
    assert result.reason_code == "aborted"
