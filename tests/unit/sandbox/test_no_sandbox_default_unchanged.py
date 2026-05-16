"""Sprint 3 Phase 2 T2.4 — golden test: BashTool byte-identical under NoSandbox.

Spec anchor: spec.md scenario "NoSandboxPolicy is the default when sandbox
settings absent" + invariant 6/7/8 (Sprint 1 BashTool contract).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from pyclaw.core.agent.tools.builtin import BashTool
from pyclaw.core.agent.tools.registry import ToolContext


@pytest.mark.asyncio
async def test_echo_returns_stdout_exit_zero(tmp_path: Path) -> None:
    tool = BashTool()
    ctx = ToolContext(
        workspace_id="default",
        workspace_path=tmp_path,
        session_id="s1",
        abort=asyncio.Event(),
    )
    result = await tool.execute({"command": "echo hello", "_call_id": "c1"}, ctx)

    text = (
        result.content[0].text
        if hasattr(result, "content") and result.content
        else getattr(result, "text", "")
    )
    assert "[stdout]\nhello\n" in text
    assert "[exit_code=0]" in text


@pytest.mark.asyncio
async def test_nonzero_exit_returned_as_error(tmp_path: Path) -> None:
    tool = BashTool()
    ctx = ToolContext(
        workspace_id="default",
        workspace_path=tmp_path,
        session_id="s1",
        abort=asyncio.Event(),
    )
    result = await tool.execute({"command": "exit 7", "_call_id": "c1"}, ctx)
    text = (
        result.content[0].text
        if hasattr(result, "content") and result.content
        else getattr(result, "text", "")
    )
    assert "[exit_code=7]" in text


@pytest.mark.asyncio
async def test_cwd_is_workspace_path(tmp_path: Path) -> None:
    """Sprint 1 invariant 6: BashTool cwd = context.workspace_path."""
    tool = BashTool()
    ctx = ToolContext(
        workspace_id="default",
        workspace_path=tmp_path,
        session_id="s1",
        abort=asyncio.Event(),
    )
    result = await tool.execute({"command": "pwd", "_call_id": "c1"}, ctx)
    text = (
        result.content[0].text
        if hasattr(result, "content") and result.content
        else getattr(result, "text", "")
    )
    assert str(tmp_path) in text


@pytest.mark.asyncio
async def test_stderr_captured(tmp_path: Path) -> None:
    """Sprint 1 invariant 8: ToolResult format [stdout]\\n...\\n[stderr]\\n...\\n[exit_code=N]"""
    tool = BashTool()
    ctx = ToolContext(
        workspace_id="default",
        workspace_path=tmp_path,
        session_id="s1",
        abort=asyncio.Event(),
    )
    result = await tool.execute(
        {"command": "echo out; echo err 1>&2; exit 0", "_call_id": "c1"},
        ctx,
    )
    text = (
        result.content[0].text
        if hasattr(result, "content") and result.content
        else getattr(result, "text", "")
    )
    assert "[stdout]\nout\n" in text
    assert "[stderr]\nerr\n" in text
    assert "[exit_code=0]" in text


@pytest.mark.asyncio
async def test_default_sandbox_policy_is_no_sandbox(tmp_path: Path) -> None:
    """ToolContext default sandbox_policy is NoSandboxPolicy (Sprint 3 backward compat)."""
    from pyclaw.sandbox.no_sandbox import NoSandboxPolicy

    ctx = ToolContext(
        workspace_id="default",
        workspace_path=tmp_path,
        session_id="s1",
    )
    assert isinstance(ctx.sandbox_policy, NoSandboxPolicy)
    assert ctx.sandbox_policy.backend == "none"
