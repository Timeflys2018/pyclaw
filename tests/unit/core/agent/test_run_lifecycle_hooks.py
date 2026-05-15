from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMResponse, LLMUsage
from pyclaw.core.agent.run_control import RunControl
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.hooks import HookRegistry


class _FakeLLM(LLMClient):
    def __init__(self, response: LLMResponse) -> None:
        super().__init__(default_model="fake-model")
        self._response = response

    async def complete(self, **_kwargs: Any) -> LLMResponse:
        return self._response

    async def stream(self, **_kwargs: Any):
        from pyclaw.core.agent.llm import LLMStreamChunk

        if self._response.text:
            yield LLMStreamChunk(text_delta=self._response.text)
        yield LLMStreamChunk(
            finish_reason=self._response.finish_reason,
            usage=self._response.usage,
        )


class _RaisingLLM(LLMClient):
    def __init__(self) -> None:
        super().__init__(default_model="fake-model")

    async def stream(self, **_kwargs: Any):
        from pyclaw.core.agent.llm import LLMError

        raise LLMError("boom", "synthetic failure")
        yield  # pragma: no cover


class _BaseStubHook:
    async def before_prompt_build(self, _ctx: Any) -> None:
        return None

    async def after_response(self, _obs: Any) -> None:
        return None

    async def before_compaction(self, _ctx: Any) -> None:
        return None

    async def after_compaction(self, _ctx: Any, _result: Any) -> None:
        return None


class _RecordingHook(_BaseStubHook):
    def __init__(self) -> None:
        self.run_starts: list[tuple[str, RunControl]] = []
        self.run_ends: list[tuple[str, str]] = []
        self.active_at_start: list[bool] = []

    async def on_run_start(self, session_id: str, control: RunControl) -> None:
        self.run_starts.append((session_id, control))
        self.active_at_start.append(control.active)

    async def on_run_end(self, session_id: str, terminated_by: str) -> None:
        self.run_ends.append((session_id, terminated_by))


class _ExplodingHook(_BaseStubHook):
    async def on_run_start(self, session_id: str, control: RunControl) -> None:
        raise RuntimeError("hook boom on_run_start")

    async def on_run_end(self, session_id: str, terminated_by: str) -> None:
        raise RuntimeError("hook boom on_run_end")


def _usage() -> LLMUsage:
    return LLMUsage(input_tokens=1, output_tokens=1, total_tokens=2)


def _build_deps(llm: LLMClient, hook: Any) -> AgentRunnerDeps:
    hooks = HookRegistry()
    hooks.register(hook)
    return AgentRunnerDeps(llm=llm, tools=ToolRegistry(), hooks=hooks)


@pytest.mark.asyncio
async def test_on_run_start_fires_once_with_control(tmp_path: Path) -> None:
    llm = _FakeLLM(LLMResponse(text="hi", tool_calls=[], usage=_usage(), finish_reason="stop"))
    hook = _RecordingHook()
    deps = _build_deps(llm, hook)

    async for _ in run_agent_stream(
        RunRequest(
            session_id="s-start", workspace_id="default", agent_id="main", user_message="ping"
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        pass

    assert len(hook.run_starts) == 1
    sid, ctrl = hook.run_starts[0]
    assert sid == "s-start"
    assert isinstance(ctrl, RunControl)


@pytest.mark.asyncio
async def test_on_run_end_fires_with_done_for_normal_completion(tmp_path: Path) -> None:
    llm = _FakeLLM(LLMResponse(text="hi", tool_calls=[], usage=_usage(), finish_reason="stop"))
    hook = _RecordingHook()
    deps = _build_deps(llm, hook)

    async for _ in run_agent_stream(
        RunRequest(
            session_id="s-done", workspace_id="default", agent_id="main", user_message="ping"
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        pass

    assert hook.run_ends == [("s-done", "done")]


@pytest.mark.asyncio
async def test_on_run_end_fires_with_error_code_on_llm_failure(tmp_path: Path) -> None:
    llm = _RaisingLLM()
    hook = _RecordingHook()
    deps = _build_deps(llm, hook)

    async for _ in run_agent_stream(
        RunRequest(
            session_id="s-err", workspace_id="default", agent_id="main", user_message="ping"
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        pass

    assert hook.run_ends == [("s-err", "boom")]


@pytest.mark.asyncio
async def test_on_run_end_fires_with_aborted_when_pre_set(tmp_path: Path) -> None:
    llm = _FakeLLM(LLMResponse(text="hi", tool_calls=[], usage=_usage(), finish_reason="stop"))
    hook = _RecordingHook()
    deps = _build_deps(llm, hook)

    control = RunControl()
    control.stop()

    async for _ in run_agent_stream(
        RunRequest(
            session_id="s-abort", workspace_id="default", agent_id="main", user_message="ping"
        ),
        deps,
        tool_workspace_path=tmp_path,
        control=control,
    ):
        pass

    assert hook.run_ends == [("s-abort", "aborted")]


@pytest.mark.asyncio
async def test_hook_exceptions_do_not_abort_run(tmp_path: Path) -> None:
    llm = _FakeLLM(LLMResponse(text="hi", tool_calls=[], usage=_usage(), finish_reason="stop"))
    deps = _build_deps(llm, _ExplodingHook())

    events = []
    async for ev in run_agent_stream(
        RunRequest(
            session_id="s-hook-explode",
            workspace_id="default",
            agent_id="main",
            user_message="ping",
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(ev)

    from pyclaw.models import Done

    assert any(isinstance(e, Done) for e in events)


@pytest.mark.asyncio
async def test_hook_does_not_modify_control_active(tmp_path: Path) -> None:
    llm = _FakeLLM(LLMResponse(text="hi", tool_calls=[], usage=_usage(), finish_reason="stop"))
    hook = _RecordingHook()
    deps = _build_deps(llm, hook)

    control = RunControl()
    assert control.active is False

    async for _ in run_agent_stream(
        RunRequest(
            session_id="s-active", workspace_id="default", agent_id="main", user_message="ping"
        ),
        deps,
        tool_workspace_path=tmp_path,
        control=control,
    ):
        pass

    assert control.active is False
    assert hook.active_at_start == [False]


@pytest.mark.asyncio
async def test_legacy_hook_without_lifecycle_methods_is_skipped(tmp_path: Path) -> None:
    class _LegacyHook:
        async def before_prompt_build(self, _ctx: Any) -> None:
            return None

        async def after_response(self, _obs: Any) -> None:
            return None

        async def before_compaction(self, _ctx: Any) -> None:
            return None

        async def after_compaction(self, _ctx: Any, _result: Any) -> None:
            return None

    llm = _FakeLLM(LLMResponse(text="ok", tool_calls=[], usage=_usage(), finish_reason="stop"))
    deps = _build_deps(llm, _LegacyHook())

    from pyclaw.models import Done

    events = []
    async for ev in run_agent_stream(
        RunRequest(
            session_id="s-legacy", workspace_id="default", agent_id="main", user_message="ping"
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(ev)

    assert any(isinstance(e, Done) for e in events)
