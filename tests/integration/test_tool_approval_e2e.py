from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.web.chat import SessionQueue, _run_chat
from pyclaw.channels.web.deps import WebDeps
from pyclaw.channels.web.protocol import (
    SERVER_CHAT_DONE,
    SERVER_CHAT_TOOL_END,
    SERVER_TOOL_APPROVE_REQUEST,
    ChatSendMessage,
)
from pyclaw.channels.web.tool_approval_hook import WebToolApprovalHook
from pyclaw.channels.web.websocket import ConnectionState
from pyclaw.core.agent.llm import LLMClient, LLMResponse, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.core.agent.tools.registry import (
    ToolContext,
    ToolRegistry,
    text_result,
)
from pyclaw.infra.audit_logger import AuditLogger
from pyclaw.infra.settings import WebSettings
from pyclaw.infra.task_manager import TaskManager
from pyclaw.models import ToolResult


class _BashLikeTool:
    name = "bash"
    description = "test bash"
    parameters = {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    }
    side_effect = True
    tool_class = "write"

    def __init__(self) -> None:
        self.executed: list[dict[str, Any]] = []

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        self.executed.append(args)
        return text_result(args.get("_call_id", "x"), "executed")


class _FakeLLM(LLMClient):
    def __init__(self, responses: list[LLMResponse]) -> None:
        super().__init__(default_model="fake-model")
        self._responses = list(responses)

    async def complete(
        self,
        *,
        messages,
        model=None,
        tools=None,
        system=None,
        idle_seconds: float = 0.0,
        abort_event=None,
    ) -> LLMResponse:
        return self._responses.pop(0)

    async def stream(
        self,
        *,
        messages,
        model=None,
        tools=None,
        system=None,
        idle_seconds: float = 0.0,
        abort_event=None,
    ):
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
                        "type": "function",
                        "function": {"name": fn.get("name", ""), "arguments": args_str},
                    }
                ]
            )
        yield LLMStreamChunk(finish_reason=response.finish_reason, usage=response.usage)


def _bash_call_response(call_id: str = "c1") -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": "bash",
                    "arguments": {"command": "ls", "_call_id": call_id},
                },
            }
        ],
        usage=LLMUsage(input_tokens=10, output_tokens=5, total_tokens=15),
        finish_reason="tool_calls",
    )


def _final_text() -> LLMResponse:
    return LLMResponse(
        text="final",
        tool_calls=[],
        usage=LLMUsage(input_tokens=5, output_tokens=3, total_tokens=8),
        finish_reason="stop",
    )


def _setup_state(
    *,
    tier_in_msg: str | None,
    timeout_seconds: int = 10,
) -> tuple[
    ConnectionState, AsyncMock, WebSettings, SessionQueue, _BashLikeTool, list[dict[str, Any]]
]:
    tm = TaskManager()
    sq = SessionQueue(task_manager=tm)
    workspace_base = Path(tempfile.mkdtemp())

    bash_tool = _BashLikeTool()
    registry = ToolRegistry()
    registry.register(bash_tool)

    fake_llm = _FakeLLM([_bash_call_response(), _final_text()])

    runner_deps = AgentRunnerDeps(llm=fake_llm, tools=registry)

    settings = WebSettings(
        jwt_secret="s",
        heartbeat_interval=60,
        pong_timeout=10,
        toolsRequiringApproval=["bash", "write", "edit"],
        toolApprovalTimeoutSeconds=timeout_seconds,
    )
    audit_logger = AuditLogger()
    hook = WebToolApprovalHook(
        session_queue=sq,
        settings=settings,
        audit_logger=audit_logger,
    )

    sent_events: list[dict[str, Any]] = []

    mock_ws = AsyncMock()
    mock_ws.app.state.workspace_base = workspace_base
    mock_ws.app.state.runner_deps = runner_deps
    mock_ws.app.state.task_manager = tm
    web_deps = MagicMock(spec=WebDeps)
    web_deps.tool_approval_hook = hook
    web_deps.session_queue = sq
    mock_ws.app.state.web_deps = web_deps

    async def fake_send_json(envelope: dict[str, Any]) -> None:
        sent_events.append(envelope)

    mock_ws.send_json = fake_send_json
    mock_ws.client_state.name = "CONNECTED"

    state = ConnectionState(
        ws=mock_ws,
        ws_session_id="ws-1",
        user_id="me",
        authenticated=True,
    )

    return state, mock_ws, settings, sq, bash_tool, sent_events


def _captured_audit(captured: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        json.loads(r.message) for r in captured.records if r.name == "pyclaw.audit.tool_approval"
    ]


@pytest.fixture
def captured(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="pyclaw.audit.tool_approval")
    return caplog


class TestApprovalE2E:
    @pytest.mark.asyncio
    async def test_approval_tier_user_approves_tool_executes(
        self,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        state, _, settings, sq, bash_tool, sent_events = _setup_state(
            tier_in_msg="approval",
        )

        async def respond_when_asked() -> None:
            for _ in range(50):
                await asyncio.sleep(0.05)
                if any(e.get("type") == SERVER_TOOL_APPROVE_REQUEST for e in sent_events):
                    sq.set_approval_decision("web:me:c1", "c1", True)
                    return

        gate = asyncio.create_task(respond_when_asked())
        msg = ChatSendMessage(
            conversation_id="web:me:c1",
            content="hi",
            tier="approval",
        )
        await _run_chat(state, msg, settings)
        await gate

        approve_requests = [e for e in sent_events if e.get("type") == SERVER_TOOL_APPROVE_REQUEST]
        assert len(approve_requests) == 1
        assert len(bash_tool.executed) == 1

        audit = _captured_audit(captured)
        assert any(r["decision"] == "approve" and r["decided_by"] == "user" for r in audit)

    @pytest.mark.asyncio
    async def test_approval_tier_user_denies_tool_skipped(
        self,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        state, _, settings, sq, bash_tool, sent_events = _setup_state(
            tier_in_msg="approval",
        )

        async def respond_when_asked() -> None:
            for _ in range(50):
                await asyncio.sleep(0.05)
                if any(e.get("type") == SERVER_TOOL_APPROVE_REQUEST for e in sent_events):
                    sq.set_approval_decision("web:me:c1", "c1", False)
                    return

        gate = asyncio.create_task(respond_when_asked())
        msg = ChatSendMessage(
            conversation_id="web:me:c1",
            content="hi",
            tier="approval",
        )
        await _run_chat(state, msg, settings)
        await gate

        assert len(bash_tool.executed) == 0
        ends = [e for e in sent_events if e.get("type") == SERVER_CHAT_TOOL_END]
        assert len(ends) == 1
        assert ends[0]["data"].get("is_error", False) or "denied" in str(ends[0]).lower()

        audit = _captured_audit(captured)
        assert any(r["decision"] == "deny" and r["decided_by"] == "user" for r in audit)

    @pytest.mark.asyncio
    async def test_approval_tier_timeout_denies(
        self,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        state, _, settings, sq, bash_tool, sent_events = _setup_state(
            tier_in_msg="approval",
            timeout_seconds=1,
        )
        msg = ChatSendMessage(
            conversation_id="web:me:c1",
            content="hi",
            tier="approval",
        )
        started = time.monotonic()
        await _run_chat(state, msg, settings)
        elapsed = time.monotonic() - started

        assert elapsed >= 1.0
        assert len(bash_tool.executed) == 0

        audit = _captured_audit(captured)
        assert any(r["decision"] == "deny" and r["decided_by"] == "auto:timeout" for r in audit)


class TestReadOnlyE2E:
    @pytest.mark.asyncio
    async def test_read_only_auto_denies_no_approval_event(
        self,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        state, _, settings, _, bash_tool, sent_events = _setup_state(
            tier_in_msg="read-only",
        )
        msg = ChatSendMessage(
            conversation_id="web:me:c1",
            content="hi",
            tier="read-only",
        )
        await _run_chat(state, msg, settings)

        approve_requests = [e for e in sent_events if e.get("type") == SERVER_TOOL_APPROVE_REQUEST]
        assert approve_requests == []
        assert len(bash_tool.executed) == 0

        ends = [e for e in sent_events if e.get("type") == SERVER_CHAT_TOOL_END]
        assert len(ends) == 1
        end_text = json.dumps(ends[0]).lower()
        assert "read-only mode" in end_text


class TestYoloE2E:
    @pytest.mark.asyncio
    async def test_yolo_skips_approval_executes_directly(
        self,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        state, _, settings, _, bash_tool, sent_events = _setup_state(
            tier_in_msg="yolo",
        )
        msg = ChatSendMessage(
            conversation_id="web:me:c1",
            content="hi",
            tier="yolo",
        )
        await _run_chat(state, msg, settings)

        approve_requests = [e for e in sent_events if e.get("type") == SERVER_TOOL_APPROVE_REQUEST]
        assert approve_requests == []
        assert len(bash_tool.executed) == 1

        done = [e for e in sent_events if e.get("type") == SERVER_CHAT_DONE]
        assert len(done) == 1
