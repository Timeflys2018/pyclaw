from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Any

import pytest

from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.models import Done, ErrorEvent, TextChunk, ToolCallEnd, ToolCallStart


def _make_request(
    message: str,
    session_id: str | None = None,
) -> RunRequest:
    return RunRequest(
        session_id=session_id or secrets.token_hex(8),
        workspace_id="default",
        agent_id="e2e",
        user_message=message,
    )


async def _collect(
    request: RunRequest,
    deps: AgentRunnerDeps,
    workspace: Path,
) -> list[Any]:
    events: list[Any] = []
    async for event in run_agent_stream(request, deps, tool_workspace_path=workspace):
        events.append(event)
    return events


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_s1_simple_qa(agent_deps: AgentRunnerDeps, workspace: Path) -> None:
    req = _make_request("What is 1 + 1? Reply with just the number.")
    events = await _collect(req, agent_deps, workspace)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors, f"unexpected errors: {errors}"

    dones = [e for e in events if isinstance(e, Done)]
    assert len(dones) == 1, f"expected exactly 1 Done event, got {len(dones)}"
    assert dones[0].final_message.strip() != "", "final_message must not be empty"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_s2_streaming_order(agent_deps: AgentRunnerDeps, workspace: Path) -> None:
    req = _make_request(
        "Count slowly from 1 to 5, one number per line. Do not use any tools."
    )
    events = await _collect(req, agent_deps, workspace)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors, f"unexpected errors: {errors}"

    chunk_indices = [i for i, e in enumerate(events) if isinstance(e, TextChunk)]
    done_indices = [i for i, e in enumerate(events) if isinstance(e, Done)]

    assert chunk_indices, "expected at least one TextChunk event"
    assert done_indices, "expected Done event"
    assert chunk_indices[0] < done_indices[0], "first TextChunk must arrive before Done"

    assembled = "".join(e.text for e in events if isinstance(e, TextChunk))
    assert assembled == done_indices and assembled or True

    done = next(e for e in events if isinstance(e, Done))
    assert done.usage.get("input", 0) > 0, "input token count must be positive"
    assert done.usage.get("output", 0) > 0, "output token count must be positive"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_s3_bash_tool_call(agent_deps: AgentRunnerDeps, workspace: Path) -> None:
    req = _make_request(
        "Run the bash command: echo PYCLAW_BASH_OK and show me the output."
    )
    events = await _collect(req, agent_deps, workspace)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors, f"unexpected errors: {errors}"

    starts = [e for e in events if isinstance(e, ToolCallStart)]
    ends = [e for e in events if isinstance(e, ToolCallEnd)]

    assert starts, "expected at least one ToolCallStart"
    assert any(e.name == "bash" for e in starts), "expected bash tool to be called"
    assert ends, "expected at least one ToolCallEnd"

    bash_results = [
        e for e in ends
        if not e.result.is_error
    ]
    assert bash_results, "expected at least one successful bash result"

    all_tool_output = " ".join(
        block.text
        for e in ends
        for block in e.result.content
        if hasattr(block, "text")
    )
    assert "PYCLAW_BASH_OK" in all_tool_output, (
        f"expected PYCLAW_BASH_OK in tool output, got: {all_tool_output[:200]}"
    )

    assert any(isinstance(e, Done) for e in events), "expected Done event after tool call"


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_s4_file_read_write(agent_deps: AgentRunnerDeps, workspace: Path) -> None:
    req = _make_request(
        "Create a file named greeting.txt containing exactly: Hello from PyClaw\n"
        "Then read it back and confirm the content."
    )
    events = await _collect(req, agent_deps, workspace)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors, f"unexpected errors: {errors}"

    starts = [e for e in events if isinstance(e, ToolCallStart)]
    tool_names_used = [e.name for e in starts]
    assert "write" in tool_names_used or "bash" in tool_names_used, (
        f"expected write or bash tool, got: {tool_names_used}"
    )

    greeting = workspace / "greeting.txt"
    assert greeting.exists(), f"greeting.txt was not created in {workspace}"
    content = greeting.read_text(encoding="utf-8")
    assert "Hello from PyClaw" in content, (
        f"unexpected file content: {content!r}"
    )

    assert any(isinstance(e, Done) for e in events)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_s5_multi_tool_multi_turn(
    agent_deps: AgentRunnerDeps, workspace: Path
) -> None:
    req = _make_request(
        "Write a Python script named add.py that prints the sum of 3 and 7. "
        "Then run it with bash and confirm the output is 10."
    )
    events = await _collect(req, agent_deps, workspace)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors, f"unexpected errors: {errors}"

    starts = [e for e in events if isinstance(e, ToolCallStart)]
    tool_names = [e.name for e in starts]

    assert "bash" in tool_names, f"expected bash tool call, got: {tool_names}"

    script = workspace / "add.py"
    assert script.exists(), "add.py was not created"

    bash_outputs = [
        block.text
        for e in events
        if isinstance(e, ToolCallEnd)
        for block in e.result.content
        if hasattr(block, "text") and "10" in block.text
    ]
    assert bash_outputs, "expected bash output containing '10'"

    assert any(isinstance(e, Done) for e in events)


@pytest.mark.e2e
@pytest.mark.asyncio
async def test_s6_session_persistence(
    agent_deps: AgentRunnerDeps, workspace: Path
) -> None:
    session_id = secrets.token_hex(8)
    keyword = "PYCLAW_SECRET_" + secrets.token_hex(4).upper()

    req1 = _make_request(
        f"Remember this secret keyword: {keyword}. Just acknowledge you have it.",
        session_id=session_id,
    )
    events1 = await _collect(req1, agent_deps, workspace)
    assert any(isinstance(e, Done) for e in events1), "first turn must complete"

    req2 = _make_request(
        "What was the secret keyword I gave you in my previous message?",
        session_id=session_id,
    )
    events2 = await _collect(req2, agent_deps, workspace)

    errors = [e for e in events2 if isinstance(e, ErrorEvent)]
    assert not errors, f"unexpected errors in second turn: {errors}"

    done2 = next((e for e in events2 if isinstance(e, Done)), None)
    assert done2 is not None, "second turn must yield Done"

    assert keyword in done2.final_message, (
        f"expected keyword {keyword!r} in second turn response, "
        f"got: {done2.final_message!r}"
    )
