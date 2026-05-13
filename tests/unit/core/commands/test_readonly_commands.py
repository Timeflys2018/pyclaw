from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.readonly import (
    cmd_context,
    cmd_queue,
    cmd_resume,
    cmd_tools,
)
from pyclaw.infra.settings import Settings
from pyclaw.models.session import (
    MessageEntry,
    SessionHeader,
    SessionHistorySummary,
    SessionTree,
    now_iso,
)


def _ctx(
    *,
    channel: str = "web",
    session_queue: Any = None,
    queue_registry: Any = None,
    last_usage: dict[str, int] | None = None,
    tools: Any = None,
    session_store: Any = None,
    session_router: Any = None,
    reply: Any = None,
) -> CommandContext:
    deps = MagicMock()
    deps.tools = tools or MagicMock()
    deps.session_store = session_store or MagicMock()
    return CommandContext(
        session_id="skey:s:0123456789abcdef",
        session_key="skey",
        workspace_id="ws",
        user_id="user_x",
        channel=channel,
        deps=deps,
        session_router=session_router or MagicMock(),
        workspace_base=Path("/tmp"),
        reply=reply or AsyncMock(),
        dispatch_user_message=AsyncMock(),
        raw={},
        settings=Settings(),
        session_queue=session_queue,
        queue_registry=queue_registry,
        last_usage=last_usage,
    )


def _make_tool(name: str, description: str, *, side_effect: bool):
    t = MagicMock()
    t.name = name
    t.description = description
    t.side_effect = side_effect
    return t


@pytest.mark.asyncio
async def test_tools_empty_registry_reports_no_tools() -> None:
    tools = MagicMock()
    tools.names.return_value = []
    reply = AsyncMock()
    await cmd_tools("", _ctx(tools=tools, reply=reply))
    reply.assert_awaited_once()
    text = reply.await_args[0][0]
    assert "无" in text or "no" in text.lower()


@pytest.mark.asyncio
async def test_tools_lists_grouped_by_side_effect() -> None:
    safe_a = _make_tool("apple", "fruit", side_effect=False)
    safe_b = _make_tool("banana", "yellow", side_effect=False)
    se_c = _make_tool("chainsaw", "dangerous", side_effect=True)
    tools = MagicMock()
    tools.names.return_value = ["chainsaw", "banana", "apple"]
    tools.get.side_effect = lambda n: {"apple": safe_a, "banana": safe_b, "chainsaw": se_c}[n]

    reply = AsyncMock()
    await cmd_tools("", _ctx(tools=tools, reply=reply))
    text = reply.await_args[0][0]

    i_apple = text.index("apple")
    i_banana = text.index("banana")
    i_chainsaw = text.index("chainsaw")
    assert i_apple < i_banana
    assert i_banana < i_chainsaw


@pytest.mark.asyncio
async def test_tools_shows_name_and_description() -> None:
    t = _make_tool("bash", "run a shell command", side_effect=True)
    tools = MagicMock()
    tools.names.return_value = ["bash"]
    tools.get.return_value = t
    reply = AsyncMock()
    await cmd_tools("", _ctx(tools=tools, reply=reply))
    text = reply.await_args[0][0]
    assert "bash" in text
    assert "run a shell command" in text


@pytest.mark.asyncio
async def test_queue_idle_on_web() -> None:
    sq = MagicMock()
    sq.queue_position.return_value = 0
    reply = AsyncMock()
    await cmd_queue("", _ctx(session_queue=sq, reply=reply))
    text = reply.await_args[0][0]
    assert "空闲" in text or "0" in text


@pytest.mark.asyncio
async def test_queue_busy_with_pending_on_web() -> None:
    sq = MagicMock()
    sq.queue_position.return_value = 3
    reply = AsyncMock()
    await cmd_queue("", _ctx(session_queue=sq, reply=reply))
    text = reply.await_args[0][0]
    assert "3" in text


@pytest.mark.asyncio
async def test_queue_busy_on_feishu() -> None:
    qr = MagicMock()
    qr.queue_position.return_value = 2
    reply = AsyncMock()
    await cmd_queue("", _ctx(channel="feishu", queue_registry=qr, reply=reply))
    text = reply.await_args[0][0]
    assert "2" in text


@pytest.mark.asyncio
async def test_queue_with_no_queue_registry_or_session_queue() -> None:
    reply = AsyncMock()
    await cmd_queue("", _ctx(reply=reply))
    text = reply.await_args[0][0]
    assert "unavailable" in text.lower() or "不可用" in text


@pytest.mark.asyncio
async def test_context_no_usage_yet() -> None:
    reply = AsyncMock()
    await cmd_context("", _ctx(reply=reply))
    text = reply.await_args[0][0]
    assert "尚未" in text or "no" in text.lower()


@pytest.mark.asyncio
async def test_context_shows_all_four_values() -> None:
    reply = AsyncMock()
    usage = {"input": 12000, "output": 1500, "cache_creation": 500, "cache_read": 8000}
    await cmd_context("", _ctx(last_usage=usage, reply=reply))
    text = reply.await_args[0][0]
    assert "12" in text and "000" in text
    assert "1500" in text or "1,500" in text
    assert "8000" in text or "8,000" in text
    assert "500" in text


@pytest.mark.asyncio
async def test_context_shows_budget_reservations_when_configured() -> None:
    settings = Settings()
    settings.agent.prompt_budget.system_zone_tokens = 30000
    settings.agent.prompt_budget.dynamic_zone_tokens = 5000
    reply = AsyncMock()
    usage = {"input": 12000, "output": 1500, "cache_creation": 0, "cache_read": 0}
    ctx = _ctx(last_usage=usage, reply=reply)
    ctx.settings = settings
    await cmd_context("", ctx)
    text = reply.await_args[0][0]
    assert "30,000" in text
    assert "5,000" in text
    assert "System zone" in text or "system zone" in text.lower()
    assert "Dynamic zone" in text or "dynamic zone" in text.lower()


@pytest.mark.asyncio
async def test_context_without_budget_omits_budget_section() -> None:
    settings = Settings()
    settings.agent.prompt_budget.system_zone_tokens = 0
    settings.agent.prompt_budget.dynamic_zone_tokens = 0
    reply = AsyncMock()
    usage = {"input": 12000, "output": 1500, "cache_creation": 0, "cache_read": 0}
    ctx = _ctx(last_usage=usage, reply=reply)
    ctx.settings = settings
    await cmd_context("", ctx)
    text = reply.await_args[0][0]
    assert "Prompt budget" not in text
    assert "System zone" not in text


@pytest.mark.asyncio
async def test_context_never_computes_misleading_input_vs_system_zone_percentage() -> None:
    settings = Settings()
    settings.agent.prompt_budget.system_zone_tokens = 4096
    reply = AsyncMock()
    usage = {"input": 4722, "output": 711, "cache_creation": 0, "cache_read": 0}
    ctx = _ctx(last_usage=usage, reply=reply)
    ctx.settings = settings
    await cmd_context("", ctx)
    text = reply.await_args[0][0]
    assert "115" not in text
    assert "%" not in text


@pytest.mark.asyncio
async def test_context_never_claims_percentage_of_budget_from_input_tokens() -> None:
    settings = Settings()
    settings.agent.prompt_budget.system_zone_tokens = 10000
    reply = AsyncMock()
    usage = {"input": 7500, "output": 500, "cache_creation": 0, "cache_read": 0}
    ctx = _ctx(last_usage=usage, reply=reply)
    ctx.settings = settings
    await cmd_context("", ctx)
    text = reply.await_args[0][0]
    assert "75%" not in text
    assert "75.0%" not in text


def _make_tree(session_id: str, n_messages: int = 3) -> SessionTree:
    header = SessionHeader(
        id=session_id,
        workspace_id="ws",
        agent_id="default",
        session_key="skey",
        created_at=now_iso(),
        last_interaction_at=now_iso(),
    )
    tree = SessionTree(header=header)
    for i in range(n_messages):
        role = "user" if i % 2 == 0 else "assistant"
        tree.append(MessageEntry(
            id=f"e{i:04x}",
            parent_id=None if i == 0 else f"e{i-1:04x}",
            role=role,
            content=f"msg {i}: example content",
        ))
    return tree


def _hist(session_id: str, created_at: str | None = None, msg_count: int = 3) -> SessionHistorySummary:
    return SessionHistorySummary(
        session_id=session_id,
        created_at=created_at or now_iso(),
        message_count=msg_count,
        last_message_at=now_iso(),
        parent_session_id=None,
    )


@pytest.mark.asyncio
async def test_resume_no_args_lists_recent_sessions() -> None:
    history = [
        _hist("skey:s:aaaa111111111111"),
        _hist("skey:s:bbbb222222222222"),
        _hist("skey:s:cccc333333333333"),
    ]
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=history)
    store.set_current_session_id = AsyncMock()
    reply = AsyncMock()
    await cmd_resume("", _ctx(session_store=store, reply=reply))
    text = reply.await_args[0][0]
    assert "[1]" in text and "[2]" in text and "[3]" in text
    assert "aaaa" in text or "11111111" in text
    store.set_current_session_id.assert_not_called()


@pytest.mark.asyncio
async def test_resume_numeric_index_switches_and_shows_tail() -> None:
    sid_target = "skey:s:bbbb222222222222"
    history = [
        _hist("skey:s:aaaa111111111111"),
        _hist(sid_target),
        _hist("skey:s:cccc333333333333"),
    ]
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=history)
    store.set_current_session_id = AsyncMock()
    store.load = AsyncMock(return_value=_make_tree(sid_target, n_messages=3))
    reply = AsyncMock()
    await cmd_resume("2", _ctx(session_store=store, reply=reply))
    store.set_current_session_id.assert_awaited_once_with("skey", sid_target)
    text = reply.await_args[0][0]
    assert "msg 0" in text or "msg 1" in text or "msg 2" in text


@pytest.mark.asyncio
async def test_resume_out_of_range_index_errors_no_switch() -> None:
    history = [_hist("skey:s:aaaa111111111111")]
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=history)
    store.set_current_session_id = AsyncMock()
    reply = AsyncMock()
    await cmd_resume("99", _ctx(session_store=store, reply=reply))
    store.set_current_session_id.assert_not_called()
    text = reply.await_args[0][0]
    assert "无效" in text or "invalid" in text.lower() or "range" in text.lower()


@pytest.mark.asyncio
async def test_resume_exact_suffix_match_switches() -> None:
    sid_target = "skey:s:abcd1234567890ef"
    history = [
        _hist("skey:s:aaaa111111111111"),
        _hist(sid_target),
    ]
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=history)
    store.set_current_session_id = AsyncMock()
    store.load = AsyncMock(return_value=_make_tree(sid_target))
    reply = AsyncMock()
    await cmd_resume("67890ef", _ctx(session_store=store, reply=reply))
    store.set_current_session_id.assert_awaited_once_with("skey", sid_target)


@pytest.mark.asyncio
async def test_resume_multiple_suffix_matches_reports_ambiguity() -> None:
    history = [
        _hist("skey:s:aaaa111111111111"),
        _hist("skey:s:bbbb111111111111"),
    ]
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=history)
    store.set_current_session_id = AsyncMock()
    reply = AsyncMock()
    await cmd_resume("1111", _ctx(session_store=store, reply=reply))
    store.set_current_session_id.assert_not_called()
    text = reply.await_args[0][0]
    assert "多个" in text or "multiple" in text.lower() or "ambig" in text.lower()


@pytest.mark.asyncio
async def test_resume_zero_suffix_matches_errors() -> None:
    history = [_hist("skey:s:aaaa111111111111")]
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=history)
    store.set_current_session_id = AsyncMock()
    reply = AsyncMock()
    await cmd_resume("xxxxxxx", _ctx(session_store=store, reply=reply))
    store.set_current_session_id.assert_not_called()
    text = reply.await_args[0][0]
    assert "找不到" in text or "not found" in text.lower() or "no match" in text.lower()


@pytest.mark.asyncio
async def test_resume_current_is_noop() -> None:
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=[])
    store.set_current_session_id = AsyncMock()
    reply = AsyncMock()
    await cmd_resume("current", _ctx(session_store=store, reply=reply))
    store.set_current_session_id.assert_not_called()
    text = reply.await_args[0][0]
    assert "当前" in text or "current" in text.lower()


@pytest.mark.asyncio
async def test_resume_expired_session_does_not_switch() -> None:
    sid_target = "skey:s:bbbb222222222222"
    history = [
        _hist("skey:s:aaaa111111111111"),
        _hist(sid_target),
    ]
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=history)
    store.set_current_session_id = AsyncMock()
    store.load = AsyncMock(return_value=None)
    reply = AsyncMock()
    await cmd_resume("2", _ctx(session_store=store, reply=reply))
    store.set_current_session_id.assert_not_called()
    text = reply.await_args[0][0]
    assert "过期" in text or "expired" in text.lower() or "not found" in text.lower()


@pytest.mark.asyncio
async def test_resume_target_with_only_tool_entries_still_switches() -> None:
    sid_target = "skey:s:bbbb222222222222"
    history = [
        _hist("skey:s:aaaa111111111111"),
        _hist(sid_target),
    ]
    tree = _make_tree(sid_target, n_messages=0)
    tree.append(MessageEntry(
        id="t001",
        parent_id=None,
        role="tool",
        content="tool output",
        tool_call_id="x",
    ))
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=history)
    store.set_current_session_id = AsyncMock()
    store.load = AsyncMock(return_value=tree)
    reply = AsyncMock()
    await cmd_resume("2", _ctx(session_store=store, reply=reply))
    store.set_current_session_id.assert_awaited_once_with("skey", sid_target)


@pytest.mark.asyncio
async def test_resume_empty_history_with_no_args_is_informational() -> None:
    store = MagicMock()
    store.list_session_history = AsyncMock(return_value=[])
    store.set_current_session_id = AsyncMock()
    reply = AsyncMock()
    await cmd_resume("", _ctx(session_store=store, reply=reply))
    store.set_current_session_id.assert_not_called()
    text = reply.await_args[0][0]
    assert "无" in text or "无历史" in text or "no sessions" in text.lower() or "empty" in text.lower()
