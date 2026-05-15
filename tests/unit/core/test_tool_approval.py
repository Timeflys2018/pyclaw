from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.llm import LLMClient, LLMResponse, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.core.agent.tools.registry import ToolContext, ToolRegistry, text_result
from pyclaw.models import (
    Done,
    ToolCallEnd,
    ToolCallStart,
    ToolResult,
)
from pyclaw.models.agent import ToolApprovalRequest


class _FakeLLM(LLMClient):
    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(default_model="fake-model")
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(  # type: ignore[override]
        self,
        *,
        messages,
        model=None,
        tools=None,
        system=None,
        idle_seconds: float = 0.0,
        abort_event=None,
    ) -> LLMResponse:
        self.calls.append({"messages": messages, "tools": tools, "system": system})
        if not self._responses:
            raise AssertionError("FakeLLM exhausted")
        return self._responses.pop(0)

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
        import json

        response = await self.complete(
            messages=messages,
            model=model,
            tools=tools,
            system=system,
            idle_seconds=idle_seconds,
            abort_event=abort_event,
        )
        if response.text:
            yield LLMStreamChunk(text_delta=response.text)
        for i, call in enumerate(response.tool_calls):
            fn = call.get("function") or {}
            args = fn.get("arguments")
            args_str = json.dumps(args) if isinstance(args, dict) else (args or "")
            yield LLMStreamChunk(
                tool_call_deltas=[
                    {
                        "index": i,
                        "id": call.get("id"),
                        "type": call.get("type", "function"),
                        "function": {"name": fn.get("name", ""), "arguments": args_str},
                    }
                ]
            )
        yield LLMStreamChunk(finish_reason=response.finish_reason, usage=response.usage)


class _EchoTool:
    name = "echo"
    description = "Echo text"
    parameters = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    side_effect = False
    tool_class = "read"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        return text_result(args.get("_call_id", "x"), f"echo:{args.get('text', '')}")


class MockApprovalHook:
    def __init__(self, decisions: list[str]) -> None:
        self.decisions = decisions
        self.called_with: tuple[list[dict], str, str] | None = None

    async def before_tool_execution(
        self,
        tool_calls: list[dict],
        session_id: str,
        tier: str = "approval",
    ) -> list[str]:
        self.called_with = (tool_calls, session_id, tier)
        return self.decisions


def _usage(in_t: int = 10, out_t: int = 5) -> LLMUsage:
    return LLMUsage(input_tokens=in_t, output_tokens=out_t, total_tokens=in_t + out_t)


def _tool_call_response(tool_name: str = "echo", call_id: str = "call_1") -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": {"text": "hi", "_call_id": call_id},
                },
            }
        ],
        usage=_usage(),
        finish_reason="tool_calls",
    )


def _text_response(text: str = "done") -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], usage=_usage(), finish_reason="stop")


class TestToolApprovalNoHook:
    @pytest.mark.asyncio
    async def test_no_hook_tool_executes_normally(self, tmp_path: Path) -> None:
        llm = _FakeLLM([_tool_call_response(), _text_response()])
        registry = ToolRegistry()
        registry.register(_EchoTool())
        deps = AgentRunnerDeps(llm=llm, tools=registry)

        events = []
        async for event in run_agent_stream(
            RunRequest(session_id="s-nohook", workspace_id="w", agent_id="a", user_message="hi"),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        starts = [e for e in events if isinstance(e, ToolCallStart)]
        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(starts) == 1
        assert len(ends) == 1
        assert "echo:hi" in ends[0].result.content[0].text
        assert any(isinstance(e, Done) for e in events)
        assert not any(isinstance(e, ToolApprovalRequest) for e in events)


class TestToolApprovalApprove:
    @pytest.mark.asyncio
    async def test_hook_approves_tool_executes(self, tmp_path: Path) -> None:
        hook = MockApprovalHook(decisions=["approve"])
        llm = _FakeLLM([_tool_call_response(), _text_response()])
        registry = ToolRegistry()
        registry.register(_EchoTool())
        deps = AgentRunnerDeps(
            llm=llm,
            tools=registry,
            tool_approval_hook=hook,
        )

        events = []
        async for event in run_agent_stream(
            RunRequest(session_id="s-approve", workspace_id="w", agent_id="a", user_message="hi"),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        assert hook.called_with is not None
        calls_arg, session_arg, tier_arg = hook.called_with
        assert session_arg == "s-approve"
        assert tier_arg == "approval"
        assert len(calls_arg) == 1
        assert calls_arg[0]["name"] == "echo"

        approval_events = [e for e in events if isinstance(e, ToolApprovalRequest)]
        assert len(approval_events) == 1
        assert approval_events[0].tool_name == "echo"

        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(ends) == 1
        assert "echo:hi" in ends[0].result.content[0].text


class TestToolApprovalDeny:
    @pytest.mark.asyncio
    async def test_hook_denies_tool_skipped(self, tmp_path: Path) -> None:
        hook = MockApprovalHook(decisions=["deny"])
        llm = _FakeLLM([_tool_call_response(), _text_response("denied fallback")])
        registry = ToolRegistry()
        registry.register(_EchoTool())
        deps = AgentRunnerDeps(
            llm=llm,
            tools=registry,
            tool_approval_hook=hook,
        )

        events = []
        async for event in run_agent_stream(
            RunRequest(session_id="s-deny", workspace_id="w", agent_id="a", user_message="hi"),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        assert hook.called_with is not None

        approval_events = [e for e in events if isinstance(e, ToolApprovalRequest)]
        assert len(approval_events) == 1

        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(ends) == 1
        assert ends[0].result.is_error
        assert "denied" in ends[0].result.content[0].text

        assert any(isinstance(e, Done) for e in events)


class TestToolApprovalHookCalledWithCorrectArgs:
    @pytest.mark.asyncio
    async def test_hook_receives_correct_args(self, tmp_path: Path) -> None:
        hook = MockApprovalHook(decisions=["approve"])
        llm = _FakeLLM([_tool_call_response("echo", "call_42"), _text_response()])
        registry = ToolRegistry()
        registry.register(_EchoTool())
        deps = AgentRunnerDeps(
            llm=llm,
            tools=registry,
            tool_approval_hook=hook,
        )

        async for _ in run_agent_stream(
            RunRequest(session_id="s-args", workspace_id="w", agent_id="a", user_message="test"),
            deps,
            tool_workspace_path=tmp_path,
        ):
            pass

        assert hook.called_with is not None
        calls_arg, session_id, tier_arg = hook.called_with
        assert session_id == "s-args"
        assert tier_arg == "approval"
        assert len(calls_arg) == 1
        assert calls_arg[0]["id"] == "call_42"
        assert calls_arg[0]["name"] == "echo"


class _BashLikeTool:
    name = "bash_like"
    description = "Write-class tool for tier tests"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    side_effect = True
    tool_class = "write"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        return text_result(args.get("_call_id", "x"), "bash_like:executed")


class _ReadLikeTool:
    name = "read_like"
    description = "Read-class tool for tier tests"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}
    side_effect = False
    tool_class = "read"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        return text_result(args.get("_call_id", "x"), "read_like:executed")


class TestPermissionTierReadOnly:
    @pytest.mark.asyncio
    async def test_read_only_auto_denies_write_class_tool(self, tmp_path: Path) -> None:
        hook = MockApprovalHook(decisions=[])
        llm = _FakeLLM(
            [
                _tool_call_response("bash_like", "call_w"),
                _text_response("ok"),
            ]
        )
        registry = ToolRegistry()
        registry.register(_BashLikeTool())
        deps = AgentRunnerDeps(llm=llm, tools=registry, tool_approval_hook=hook)

        events = []
        async for event in run_agent_stream(
            RunRequest(
                session_id="s-ro-deny",
                workspace_id="w",
                agent_id="a",
                user_message="hi",
                permission_tier_override="read-only",
            ),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        assert hook.called_with is None
        approval_events = [e for e in events if isinstance(e, ToolApprovalRequest)]
        assert approval_events == []

        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(ends) == 1
        assert ends[0].result.is_error
        text = ends[0].result.content[0].text
        assert "read-only mode" in text
        assert "input footer" in text

    @pytest.mark.asyncio
    async def test_read_only_allows_read_class_tool(self, tmp_path: Path) -> None:
        hook = MockApprovalHook(decisions=[])
        llm = _FakeLLM(
            [
                _tool_call_response("read_like", "call_r"),
                _text_response("ok"),
            ]
        )
        registry = ToolRegistry()
        registry.register(_ReadLikeTool())
        deps = AgentRunnerDeps(llm=llm, tools=registry, tool_approval_hook=hook)

        events = []
        async for event in run_agent_stream(
            RunRequest(
                session_id="s-ro-allow",
                workspace_id="w",
                agent_id="a",
                user_message="hi",
                permission_tier_override="read-only",
            ),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        assert hook.called_with is None
        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(ends) == 1
        assert not ends[0].result.is_error
        assert "read_like:executed" in ends[0].result.content[0].text


class TestPermissionTierYolo:
    @pytest.mark.asyncio
    async def test_yolo_skips_hook_for_write_class(self, tmp_path: Path) -> None:
        hook = MockApprovalHook(decisions=["deny"])
        llm = _FakeLLM(
            [
                _tool_call_response("bash_like", "call_y"),
                _text_response("ok"),
            ]
        )
        registry = ToolRegistry()
        registry.register(_BashLikeTool())
        deps = AgentRunnerDeps(llm=llm, tools=registry, tool_approval_hook=hook)

        events = []
        async for event in run_agent_stream(
            RunRequest(
                session_id="s-yolo",
                workspace_id="w",
                agent_id="a",
                user_message="hi",
                permission_tier_override="yolo",
            ),
            deps,
            tool_workspace_path=tmp_path,
        ):
            events.append(event)

        assert hook.called_with is None
        approval_events = [e for e in events if isinstance(e, ToolApprovalRequest)]
        assert approval_events == []

        ends = [e for e in events if isinstance(e, ToolCallEnd)]
        assert len(ends) == 1
        assert not ends[0].result.is_error
        assert "bash_like:executed" in ends[0].result.content[0].text


class TestRunnerEmitsAuditForReadOnlyAndYolo:
    @pytest.mark.asyncio
    async def test_read_only_deny_writes_audit_with_auto_read_only(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging
        from pyclaw.infra.audit_logger import AuditLogger
        caplog.set_level(logging.INFO, logger="pyclaw.audit.tool_approval")

        llm = _FakeLLM([_tool_call_response("bash_like", "call_w"), _text_response("ok")])
        registry = ToolRegistry()
        registry.register(_BashLikeTool())
        deps = AgentRunnerDeps(
            llm=llm, tools=registry,
            audit_logger=AuditLogger(),
            channel="web",
        )
        async for _ in run_agent_stream(
            RunRequest(
                session_id="s-ro-audit", workspace_id="w", agent_id="a",
                user_message="hi", permission_tier_override="read-only",
            ),
            deps, tool_workspace_path=tmp_path,
        ):
            pass

        import json
        records = [
            json.loads(r.message)
            for r in caplog.records
            if r.name == "pyclaw.audit.tool_approval"
        ]
        deny = [r for r in records if r["decision"] == "deny"]
        assert len(deny) == 1
        assert deny[0]["decided_by"] == "auto:read-only"
        assert deny[0]["tier"] == "read-only"
        assert deny[0]["channel"] == "web"
        assert deny[0]["tool_name"] == "bash_like"

    @pytest.mark.asyncio
    async def test_yolo_writes_audit_with_auto_yolo(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture,
    ) -> None:
        import logging
        from pyclaw.infra.audit_logger import AuditLogger
        caplog.set_level(logging.INFO, logger="pyclaw.audit.tool_approval")

        llm = _FakeLLM([_tool_call_response("bash_like", "call_y"), _text_response("ok")])
        registry = ToolRegistry()
        registry.register(_BashLikeTool())
        deps = AgentRunnerDeps(
            llm=llm, tools=registry,
            audit_logger=AuditLogger(),
            channel="web",
        )
        async for _ in run_agent_stream(
            RunRequest(
                session_id="s-yolo-audit", workspace_id="w", agent_id="a",
                user_message="hi", permission_tier_override="yolo",
            ),
            deps, tool_workspace_path=tmp_path,
        ):
            pass

        import json
        records = [
            json.loads(r.message)
            for r in caplog.records
            if r.name == "pyclaw.audit.tool_approval"
        ]
        approves = [r for r in records if r["decision"] == "approve"]
        assert len(approves) == 1
        assert approves[0]["decided_by"] == "auto:yolo"
        assert approves[0]["tier"] == "yolo"


class TestPermissionTierApprovalDefault:
    @pytest.mark.asyncio
    async def test_no_override_uses_approval_tier(self, tmp_path: Path) -> None:
        hook = MockApprovalHook(decisions=["approve"])
        llm = _FakeLLM([_tool_call_response(), _text_response()])
        registry = ToolRegistry()
        registry.register(_EchoTool())
        deps = AgentRunnerDeps(llm=llm, tools=registry, tool_approval_hook=hook)

        async for _ in run_agent_stream(
            RunRequest(session_id="s-default", workspace_id="w", agent_id="a", user_message="hi"),
            deps,
            tool_workspace_path=tmp_path,
        ):
            pass

        assert hook.called_with is not None
        _, _, tier_arg = hook.called_with
        assert tier_arg == "approval"
