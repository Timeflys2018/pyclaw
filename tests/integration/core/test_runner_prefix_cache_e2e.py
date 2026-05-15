from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.models import AgentRunConfig, Done, PromptBudgetConfig


class _CapturingLLM(LLMClient):
    def __init__(self, default_model: str = "fake-model") -> None:
        super().__init__(default_model=default_model)
        self.captured_system: Any = None
        self.captured_messages: Any = None

    async def stream(  # type: ignore[override]
        self,
        *,
        messages,
        model=None,
        tools=None,
        system=None,
        idle_seconds: float = 0.0,
        abort_event=None,
        temperature=None,
    ):
        self.captured_system = system
        self.captured_messages = messages
        yield LLMStreamChunk(text_delta="ok")
        yield LLMStreamChunk(
            finish_reason="stop",
            usage=LLMUsage(
                input_tokens=100,
                output_tokens=20,
                total_tokens=120,
                cache_creation_input_tokens=80,
                cache_read_input_tokens=600,
            ),
        )


def _force_long_frozen_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    from pyclaw.core.agent import system_prompt as sp

    long_identity = "You are PyClaw, a multi-channel AI assistant with tool access. " + (
        "PADDING " * 800
    )

    real_init = sp.PromptInputs.__init__

    def patched_init(self, *args, **kwargs):
        kwargs.setdefault("identity", long_identity)
        if "identity" in kwargs and kwargs["identity"].startswith("You are PyClaw"):
            kwargs["identity"] = long_identity
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(sp.PromptInputs, "__init__", patched_init)


class TestRunnerCacheControlInjection:
    async def test_anthropic_model_receives_content_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_long_frozen_prefix(monkeypatch)
        llm = _CapturingLLM(default_model="anthropic/claude-sonnet-4-20250514")
        deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry())

        async for _ in run_agent_stream(
            RunRequest(
                session_id="s1",
                workspace_id="default",
                agent_id="main",
                user_message="hi",
            ),
            deps,
            tool_workspace_path=tmp_path,
        ):
            pass

        assert isinstance(llm.captured_system, list), (
            f"Expected list[dict] system, got {type(llm.captured_system).__name__}"
        )
        assert len(llm.captured_system) >= 1
        first_block = llm.captured_system[0]
        assert first_block["type"] == "text"
        assert first_block["cache_control"] == {"type": "ephemeral"}

    async def test_non_anthropic_model_receives_string(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _force_long_frozen_prefix(monkeypatch)
        llm = _CapturingLLM(default_model="gpt-4o")
        deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry())

        async for _ in run_agent_stream(
            RunRequest(
                session_id="s2",
                workspace_id="default",
                agent_id="main",
                user_message="hi",
            ),
            deps,
            tool_workspace_path=tmp_path,
        ):
            pass

        assert isinstance(llm.captured_system, str), (
            f"Expected str system, got {type(llm.captured_system).__name__}"
        )


class TestDoneEventCachePropagation:
    async def test_done_event_has_cache_keys(self, tmp_path: Path) -> None:
        llm = _CapturingLLM(default_model="fake-model")
        deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry())

        events = []
        async for event in run_agent_stream(
            RunRequest(
                session_id="s3",
                workspace_id="default",
                agent_id="main",
                user_message="hi",
            ),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        done_events = [e for e in events if isinstance(e, Done)]
        assert len(done_events) == 1
        usage = done_events[0].usage
        assert "input" in usage
        assert "output" in usage
        assert "cache_creation" in usage
        assert "cache_read" in usage
        assert usage["cache_creation"] == 80
        assert usage["cache_read"] == 600


class TestModelMaxOutputFlow:
    async def test_get_model_info_value_flows_to_history_budget(self, tmp_path: Path) -> None:
        llm = _CapturingLLM(default_model="fake-model")
        prompt_budget = PromptBudgetConfig(
            system_zone_tokens=4000,
            dynamic_zone_tokens=4000,
            output_reserve_tokens=None,
        )
        deps = AgentRunnerDeps(
            llm=llm,
            tools=ToolRegistry(),
            config=AgentRunConfig(
                context_window=200_000,
                prompt_budget=prompt_budget,
            ),
        )

        captured: list[dict[str, Any]] = []
        original_compute = PromptBudgetConfig.compute_history_budget

        def spy_compute(self, max_context_tokens, model_max_output=None):
            result = original_compute(self, max_context_tokens, model_max_output=model_max_output)
            captured.append({"max_output": model_max_output, "result": result})
            return result

        with (
            patch.object(PromptBudgetConfig, "compute_history_budget", spy_compute),
            patch("litellm.get_model_info", return_value={"max_output_tokens": 8192}),
        ):
            async for _ in run_agent_stream(
                RunRequest(
                    session_id="s4",
                    workspace_id="default",
                    agent_id="main",
                    user_message="hi",
                ),
                deps,
                tool_workspace_path=tmp_path,
            ):
                pass

        assert len(captured) >= 1
        call = captured[0]
        assert call["max_output"] == 8192
        assert call["result"] == 200_000 - 4000 - 4000 - 8192

    async def test_get_model_info_failure_falls_back(self, tmp_path: Path) -> None:
        llm = _CapturingLLM(default_model="custom/proxy-model")
        prompt_budget = PromptBudgetConfig(
            system_zone_tokens=4000,
            dynamic_zone_tokens=4000,
            output_reserve_ratio=0.5,
        )
        deps = AgentRunnerDeps(
            llm=llm,
            tools=ToolRegistry(),
            config=AgentRunConfig(
                context_window=100_000,
                prompt_budget=prompt_budget,
            ),
        )

        captured: list[dict[str, Any]] = []
        original_compute = PromptBudgetConfig.compute_history_budget

        def spy_compute(self, max_context_tokens, model_max_output=None):
            result = original_compute(self, max_context_tokens, model_max_output=model_max_output)
            captured.append({"max_output": model_max_output, "result": result})
            return result

        with (
            patch.object(PromptBudgetConfig, "compute_history_budget", spy_compute),
            patch("litellm.get_model_info", side_effect=Exception("model not mapped")),
        ):
            async for _ in run_agent_stream(
                RunRequest(
                    session_id="s5",
                    workspace_id="default",
                    agent_id="main",
                    user_message="hi",
                ),
                deps,
                tool_workspace_path=tmp_path,
            ):
                pass

        assert len(captured) >= 1
        assert captured[0]["max_output"] is None
        remaining = 100_000 - 4000 - 4000
        expected_reserve = int(remaining * 0.5)
        assert captured[0]["result"] == remaining - expected_reserve

    async def test_explicit_output_reserve_overrides_model_max_output(self, tmp_path: Path) -> None:
        llm = _CapturingLLM(default_model="fake-model")
        prompt_budget = PromptBudgetConfig(
            system_zone_tokens=4000,
            dynamic_zone_tokens=4000,
            output_reserve_tokens=50_000,
        )
        deps = AgentRunnerDeps(
            llm=llm,
            tools=ToolRegistry(),
            config=AgentRunConfig(
                context_window=200_000,
                prompt_budget=prompt_budget,
            ),
        )

        captured: list[dict[str, Any]] = []
        original_compute = PromptBudgetConfig.compute_history_budget

        def spy_compute(self, max_context_tokens, model_max_output=None):
            result = original_compute(self, max_context_tokens, model_max_output=model_max_output)
            captured.append({"max_output": model_max_output, "result": result})
            return result

        with (
            patch.object(PromptBudgetConfig, "compute_history_budget", spy_compute),
            patch("litellm.get_model_info", return_value={"max_output_tokens": 8192}),
        ):
            async for _ in run_agent_stream(
                RunRequest(
                    session_id="s6",
                    workspace_id="default",
                    agent_id="main",
                    user_message="hi",
                ),
                deps,
                tool_workspace_path=tmp_path,
            ):
                pass

        assert len(captured) >= 1
        assert captured[0]["max_output"] == 8192
        assert captured[0]["result"] == 200_000 - 4000 - 4000 - 50_000
