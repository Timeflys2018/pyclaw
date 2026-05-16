"""Sprint 3 V2.3b — explicit assertion of 11 backward-compat invariants.

4-slot review F7 fix: regression count alone is a proxy, not a substitute.
This file pins each Sprint 1+2+2.0.1 invariant by name so a future Phase 3+
refactor can't silently break them.

Invariant list (design.md §9 + spec.md trailer):
1. Sprint 2.0.1 should_gate(name) -> bool predicate (sync, read-only)
2. Sprint 2.0.1 actually_gated partition (runner emits ToolApprovalRequest only for gated)
3. Sprint 2 forced_tier de-escalation only (_RANK algorithm unchanged)
4. Sprint 2 tier_source field literal "forced-by-server-config"
5. Sprint 1 WorkspaceResolver.resolve_within path traversal protection
6. Sprint 1 BashTool cwd = context.workspace_path
7. Sprint 1 abort/timeout (SIGTERM → 2s grace → SIGKILL)
8. Sprint 1 ToolResult format [stdout]/[stderr]/[exit_code=N]
9. Sprint 1 sessionKey-based override (binds sessionKey not sessionId)
10. Web web_{user_id} + Feishu feishu_{app}_{open_id} workspace naming
11. MCP subprocess independence (StdioServerParameters wrapping not BashTool)
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path

import pytest


def test_invariant_01_should_gate_signature_sync() -> None:
    from pyclaw.channels.feishu.tool_approval_hook import FeishuToolApprovalHook
    from pyclaw.channels.web.tool_approval_hook import WebToolApprovalHook

    web_sig = inspect.signature(WebToolApprovalHook.should_gate)
    feishu_sig = inspect.signature(FeishuToolApprovalHook.should_gate)
    assert "tool_name" in web_sig.parameters
    assert "tool_name" in feishu_sig.parameters
    assert not inspect.iscoroutinefunction(WebToolApprovalHook.should_gate)
    assert not inspect.iscoroutinefunction(FeishuToolApprovalHook.should_gate)


def test_invariant_02_actually_gated_partition_in_runner() -> None:
    src = Path("src/pyclaw/core/agent/runner.py").read_text()
    assert "actually_gated" in src
    assert "ToolApprovalRequest" in src
    assert "auto:not-gated" in src


def test_invariant_03_forced_tier_de_escalation_rank_unchanged() -> None:
    src = Path("src/pyclaw/core/agent/runner.py").read_text()
    assert '"read-only": 2' in src
    assert '"approval": 1' in src
    assert '"yolo": 0' in src
    assert "_RANK[forced] > _RANK[per_turn_tier]" in src


def test_invariant_04_tier_source_literal_preserved() -> None:
    src = Path("src/pyclaw/core/agent/runner.py").read_text()
    assert '"forced-by-server-config"' in src


def test_invariant_05_workspace_resolver_path_protection(tmp_path: Path) -> None:
    from pyclaw.core.agent.tools.workspace import (
        WorkspaceBoundaryError,
        WorkspaceResolver,
    )
    from pyclaw.models import WorkspaceConfig

    resolver = WorkspaceResolver(WorkspaceConfig(default=str(tmp_path)))
    with pytest.raises(WorkspaceBoundaryError):
        resolver.resolve_within(tmp_path, "../etc/passwd")


@pytest.mark.asyncio
async def test_invariant_06_bash_cwd_is_workspace_path(tmp_path: Path) -> None:
    from pyclaw.core.agent.tools.builtin import BashTool
    from pyclaw.core.agent.tools.registry import ToolContext

    tool = BashTool()
    ctx = ToolContext(
        workspace_id="default",
        workspace_path=tmp_path,
        session_id="s1",
        abort=asyncio.Event(),
    )
    result = await tool.execute({"command": "pwd", "_call_id": "c1"}, ctx)
    text = result.content[0].text if result.content else ""
    assert str(tmp_path) in text


def test_invariant_07_abort_grace_constant() -> None:
    from pyclaw.core.agent.tools.builtin import BASH_ABORT_GRACE_SECONDS

    assert BASH_ABORT_GRACE_SECONDS == 2.0


@pytest.mark.asyncio
async def test_invariant_08_tool_result_format(tmp_path: Path) -> None:
    from pyclaw.core.agent.tools.builtin import BashTool
    from pyclaw.core.agent.tools.registry import ToolContext

    tool = BashTool()
    ctx = ToolContext(
        workspace_id="default",
        workspace_path=tmp_path,
        session_id="s1",
        abort=asyncio.Event(),
    )
    result = await tool.execute(
        {"command": "echo o; echo e 1>&2", "_call_id": "c1"}, ctx
    )
    text = result.content[0].text if result.content else ""
    assert "[stdout]\n" in text
    assert "[stderr]\n" in text
    assert "[exit_code=0]" in text


def test_invariant_09_sessionkey_tier_store_prefix_unchanged() -> None:
    from pyclaw.core.commands.tier_store import _KEY_PREFIX

    assert _KEY_PREFIX == "pyclaw:feishu:tier"


def test_invariant_10_workspace_naming_conventions() -> None:
    web_src = Path("src/pyclaw/channels/web/chat.py").read_text()
    assert 'f"web_{state.user_id}"' in web_src

    feishu_src = Path("src/pyclaw/channels/feishu/handler.py").read_text()
    assert "session_key.replace" in feishu_src


def test_invariant_11_mcp_uses_stdio_params_not_bashtool() -> None:
    src = Path("src/pyclaw/integrations/mcp/client_manager.py").read_text()
    assert "StdioServerParameters" in src
    assert "BashTool" not in src


def test_invariant_sprint3_phase2_no_sandbox_default() -> None:
    from pyclaw.core.agent.tools.registry import ToolContext
    from pyclaw.sandbox.no_sandbox import NoSandboxPolicy

    ctx = ToolContext(workspace_id="x", workspace_path=Path("/tmp"), session_id="s")
    assert isinstance(ctx.sandbox_policy, NoSandboxPolicy)
    assert ctx.sandbox_policy.backend == "none"
