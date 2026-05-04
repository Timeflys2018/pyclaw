from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMResponse, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolRegistry
from pyclaw.core.hooks import SkillProvider
from pyclaw.models import AgentRunConfig, Done, ErrorEvent, PromptBudgetConfig


class _FakeLLM(LLMClient):
    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(default_model="fake-model")
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def stream(self, *, messages, model=None, tools=None, system=None, idle_seconds=0.0, abort_event=None):  # type: ignore[override]
        self.calls.append({"messages": messages, "system": system})
        if not self._responses:
            raise AssertionError("FakeLLM exhausted")
        resp = self._responses.pop(0)
        if resp.text:
            yield LLMStreamChunk(text_delta=resp.text)
        yield LLMStreamChunk(finish_reason=resp.finish_reason, usage=resp.usage)


def _usage(inp: int = 100, out: int = 50) -> LLMUsage:
    return LLMUsage(input_tokens=inp, output_tokens=out, total_tokens=inp + out)


class _MockSkillProvider:
    def __init__(self, prompt: str = "<available_skills>mock</available_skills>") -> None:
        self._prompt = prompt

    def resolve_skills_prompt(self, workspace_path: str) -> str | None:
        return self._prompt


class TestSkillProviderIntegration:
    async def test_skills_prompt_appears_in_system_message(self, tmp_path: Path) -> None:
        llm = _FakeLLM([LLMResponse(text="ok", tool_calls=[], usage=_usage(), finish_reason="stop")])
        provider = _MockSkillProvider("<skills>test-skill</skills>")

        deps = AgentRunnerDeps(
            llm=llm,
            tools=ToolRegistry(),
            skill_provider=provider,
        )

        events = []
        async for ev in run_agent_stream(
            RunRequest(session_id="s1", workspace_id="default", agent_id="main", user_message="hi"),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(ev)

        assert any(isinstance(e, Done) for e in events)
        assert len(llm.calls) == 1
        system_sent = llm.calls[0]["system"]
        assert "<skills>test-skill</skills>" in system_sent

    async def test_skills_prompt_in_frozen_prefix_not_per_turn(self, tmp_path: Path) -> None:
        llm = _FakeLLM([
            LLMResponse(
                text="",
                tool_calls=[{
                    "id": "c1", "type": "function",
                    "function": {"name": "unknown_tool", "arguments": "{}"},
                }],
                usage=_usage(),
                finish_reason="tool_calls",
            ),
            LLMResponse(text="done", tool_calls=[], usage=_usage(), finish_reason="stop"),
        ])
        provider = _MockSkillProvider("FROZEN_SKILLS_MARKER")
        registry = ToolRegistry()

        deps = AgentRunnerDeps(
            llm=llm,
            tools=registry,
            skill_provider=provider,
            config=AgentRunConfig(max_iterations=5),
        )

        async for _ in run_agent_stream(
            RunRequest(session_id="s2", workspace_id="default", agent_id="main", user_message="test"),
            deps,
            tool_workspace_path=tmp_path,
        ):
            pass

        assert len(llm.calls) >= 2
        for call in llm.calls:
            assert "FROZEN_SKILLS_MARKER" in call["system"]

    async def test_no_skill_provider_still_works(self, tmp_path: Path) -> None:
        llm = _FakeLLM([LLMResponse(text="ok", tool_calls=[], usage=_usage(), finish_reason="stop")])
        deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry(), skill_provider=None)

        events = []
        async for ev in run_agent_stream(
            RunRequest(session_id="s3", workspace_id="default", agent_id="main", user_message="hi"),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(ev)

        assert any(isinstance(e, Done) for e in events)


class TestTokenLogging:
    async def test_token_usage_logged_per_turn(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        llm = _FakeLLM([
            LLMResponse(text="ok", tool_calls=[], usage=_usage(200, 80), finish_reason="stop"),
        ])
        deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry())

        with caplog.at_level(logging.INFO, logger="pyclaw.core.agent.runner"):
            async for _ in run_agent_stream(
                RunRequest(session_id="s4", workspace_id="default", agent_id="main", user_message="hi"),
                deps,
                tool_workspace_path=tmp_path,
            ):
                pass

        token_logs = [r for r in caplog.records if "token_usage" in r.message]
        assert len(token_logs) == 1
        msg = token_logs[0].message
        assert "turn=1" in msg
        assert "frozen=" in msg
        assert "per_turn=" in msg
        assert "history=" in msg
        assert "input=200" in msg
        assert "output=80" in msg
        assert "budget_remaining=" in msg

    async def test_token_log_per_iteration_in_multi_turn(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        llm = _FakeLLM([
            LLMResponse(
                text="",
                tool_calls=[{
                    "id": "c1", "type": "function",
                    "function": {"name": "unknown", "arguments": "{}"},
                }],
                usage=_usage(100, 20),
                finish_reason="tool_calls",
            ),
            LLMResponse(text="done", tool_calls=[], usage=_usage(150, 30), finish_reason="stop"),
        ])
        deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry(), config=AgentRunConfig(max_iterations=10))

        with caplog.at_level(logging.INFO, logger="pyclaw.core.agent.runner"):
            async for _ in run_agent_stream(
                RunRequest(session_id="s5", workspace_id="default", agent_id="main", user_message="test"),
                deps,
                tool_workspace_path=tmp_path,
            ):
                pass

        token_logs = [r for r in caplog.records if "token_usage" in r.message]
        assert len(token_logs) >= 2
        assert "turn=1" in token_logs[0].message
        assert "turn=2" in token_logs[1].message

    async def test_bootstrap_tokens_included_in_log(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        llm = _FakeLLM([LLMResponse(text="ok", tool_calls=[], usage=_usage(), finish_reason="stop")])

        from pyclaw.core.context_engine import DefaultContextEngine
        from pyclaw.storage.workspace.file import FileWorkspaceStore

        ws = FileWorkspaceStore(base_dir=tmp_path)
        workspace_id = "default"
        await ws.put_file(workspace_id, "AGENTS.md", "You are a helpful assistant. " * 20)

        engine = DefaultContextEngine(workspace_store=ws, bootstrap_files=["AGENTS.md"])
        deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry(), context_engine=engine)

        session_id = f"{workspace_id}:s:test6"
        with caplog.at_level(logging.INFO, logger="pyclaw.core.agent.runner"):
            async for _ in run_agent_stream(
                RunRequest(session_id=session_id, workspace_id=workspace_id, agent_id="main", user_message="hi"),
                deps,
                tool_workspace_path=tmp_path,
            ):
                pass

        token_logs = [r for r in caplog.records if "token_usage" in r.message]
        assert len(token_logs) == 1
        assert "bootstrap=" in token_logs[0].message
        bootstrap_val = int(token_logs[0].message.split("bootstrap=")[1].split(" ")[0])
        assert bootstrap_val > 0


class TestHistoryBudget:
    async def test_history_budget_passed_to_assemble(self, tmp_path: Path) -> None:
        llm = _FakeLLM([LLMResponse(text="ok", tool_calls=[], usage=_usage(), finish_reason="stop")])

        budget_config = PromptBudgetConfig(
            system_zone_tokens=4096,
            dynamic_zone_tokens=4096,
            output_reserve_tokens=128000,
        )
        config = AgentRunConfig(context_window=1_000_000, prompt_budget=budget_config)

        from unittest.mock import AsyncMock, patch

        original_assemble = None
        captured_token_budget = []

        from pyclaw.core.context_engine import DefaultContextEngine

        engine = DefaultContextEngine()
        original_assemble = engine.assemble

        async def spy_assemble(**kwargs):
            captured_token_budget.append(kwargs.get("token_budget"))
            return await original_assemble(**kwargs)

        engine.assemble = spy_assemble  # type: ignore[assignment]

        deps = AgentRunnerDeps(llm=llm, tools=ToolRegistry(), config=config, context_engine=engine)

        async for _ in run_agent_stream(
            RunRequest(session_id="s7", workspace_id="default", agent_id="main", user_message="hi"),
            deps,
            tool_workspace_path=tmp_path,
        ):
            pass

        expected_history = budget_config.compute_history_budget(1_000_000)
        assert expected_history == 863808
        assert len(captured_token_budget) == 1
        assert captured_token_budget[0] == expected_history
