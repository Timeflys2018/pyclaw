from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.infra.settings import Settings
from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.commands.builtin import cmd_compact
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.hooks import HookRegistry
from pyclaw.models import (
    CompactResult,
    CompactionEntry,
    MessageEntry,
    SessionHeader,
    SessionTree,
    generate_entry_id,
    now_iso,
)
from pyclaw.models.config import AgentRunConfig
from pyclaw.storage.session.base import InMemorySessionStore


class _FakeRedis:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int = 0) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True


class _RecordingHook:
    def __init__(self) -> None:
        self.before_called = False
        self.after_called = False
        self.after_result: CompactResult | None = None

    async def before_prompt_build(self, _ctx: Any) -> None:
        return None

    async def after_response(self, _obs: Any) -> None:
        return None

    async def before_compaction(self, _ctx: Any) -> None:
        self.before_called = True

    async def after_compaction(self, _ctx: Any, result: CompactResult) -> None:
        self.after_called = True
        self.after_result = result


class _FakeContextEngine:
    def __init__(self, result: CompactResult) -> None:
        self._result = result
        self.compact_called_with: dict[str, Any] = {}

    async def compact(self, **kwargs: Any) -> CompactResult:
        self.compact_called_with = kwargs
        return self._result


async def _populate_session(store: InMemorySessionStore, sid: str, n: int = 3) -> SessionTree:
    header = SessionHeader(id=sid, workspace_id="ws", agent_id="default", session_key="key")
    tree = SessionTree(header=header)
    await store.save_header(tree)
    parent = None
    for i in range(n):
        eid = generate_entry_id(set(tree.entries.keys()))
        entry = MessageEntry(
            id=eid,
            parent_id=parent,
            timestamp=now_iso(),
            role="user" if i % 2 == 0 else "assistant",
            content=f"message {i}",
        )
        tree.entries[entry.id] = entry
        tree.order.append(entry.id)
        tree.leaf_id = entry.id
        await store.append_entry(sid, entry, leaf_id=entry.id)
        parent = entry.id
    return tree


async def _make_ctx(
    *,
    redis_client: Any = None,
    compact_result: CompactResult | None = None,
    populate: bool = True,
    hook: _RecordingHook | None = None,
) -> tuple[CommandContext, AsyncMock, InMemorySessionStore, _FakeContextEngine, _RecordingHook]:
    store = InMemorySessionStore()
    if populate:
        await _populate_session(store, "sid-c")

    if compact_result is None:
        compact_result = CompactResult(
            ok=True, compacted=True, summary="summary", tokens_before=200, tokens_after=50,
        )
    fake_engine = _FakeContextEngine(compact_result)

    hooks = HookRegistry()
    used_hook = hook or _RecordingHook()
    hooks.register(used_hook)

    deps = MagicMock()
    deps.session_store = store
    deps.context_engine = fake_engine
    deps.hooks = hooks
    deps.config = AgentRunConfig()
    deps.llm = MagicMock()
    deps.llm.default_model = "gpt-4o"

    reply = AsyncMock()
    ctx = CommandContext(
        session_id="sid-c",
        session_key="key",
        workspace_id="ws",
        user_id="u",
        channel="web",
        deps=deps,
        session_router=SessionRouter(store=store),
        workspace_base=Path("/tmp"),
        reply=reply,
        dispatch_user_message=AsyncMock(),
        raw={"channel": "web"},
        settings=Settings(),
        redis_client=redis_client,
    )
    return ctx, reply, store, fake_engine, used_hook


@pytest.mark.asyncio
async def test_compact_normal_flow_persists_entry_and_replies_savings() -> None:
    ctx, reply, store, engine, hook = await _make_ctx()

    await cmd_compact("", ctx)

    assert hook.before_called is True
    assert hook.after_called is True
    assert engine.compact_called_with["force"] is True

    tree = await store.load("sid-c")
    assert tree is not None
    comp_entries = [e for e in tree.entries.values() if isinstance(e, CompactionEntry)]
    assert len(comp_entries) == 1
    assert comp_entries[0].summary == "summary"

    msg = reply.await_args[0][0]
    assert "✓" in msg
    assert "150" in msg


@pytest.mark.asyncio
async def test_compact_redis_cooldown_blocks_second_call() -> None:
    redis = _FakeRedis()
    ctx, reply, _, engine, _ = await _make_ctx(redis_client=redis)

    await cmd_compact("", ctx)
    first_msg = reply.await_args[0][0]
    assert "✓" in first_msg

    reply.reset_mock()
    engine.compact_called_with = {}

    await cmd_compact("", ctx)
    second_msg = reply.await_args[0][0]
    assert "冷却中" in second_msg
    assert engine.compact_called_with == {}


@pytest.mark.asyncio
async def test_compact_empty_session_friendly_reply() -> None:
    store = InMemorySessionStore()
    header = SessionHeader(id="sid-empty", workspace_id="ws", agent_id="default", session_key="key")
    await store.save_header(SessionTree(header=header))

    fake_engine = _FakeContextEngine(CompactResult(ok=True, compacted=False))
    deps = MagicMock()
    deps.session_store = store
    deps.context_engine = fake_engine
    deps.hooks = HookRegistry()
    deps.config = AgentRunConfig()
    deps.llm = MagicMock()
    deps.llm.default_model = "gpt-4o"

    reply = AsyncMock()
    ctx = CommandContext(
        session_id="sid-empty",
        session_key="key",
        workspace_id="ws",
        user_id="u",
        channel="web",
        deps=deps,
        session_router=SessionRouter(store=store),
        workspace_base=Path("/tmp"),
        reply=reply,
        dispatch_user_message=AsyncMock(),
        raw={"channel": "web"},
        settings=Settings(),
    )

    await cmd_compact("", ctx)

    msg = reply.await_args[0][0]
    assert "没有可压缩" in msg


@pytest.mark.asyncio
async def test_compact_with_focus_argument_injects_system_message() -> None:
    ctx, _, _, engine, _ = await _make_ctx()

    await cmd_compact("重点保留架构决策", ctx)

    messages = engine.compact_called_with.get("messages")
    assert messages is not None
    assert len(messages) >= 1
    first = messages[0]
    assert first["role"] == "system"
    assert "重点保留架构决策" in first["content"]


@pytest.mark.asyncio
async def test_compact_failure_replies_warning_and_does_not_persist_entry() -> None:
    ctx, reply, store, _, _ = await _make_ctx(
        compact_result=CompactResult(
            ok=False, compacted=False, reason="model timeout", reason_code="timeout",
        )
    )

    await cmd_compact("", ctx)

    msg = reply.await_args[0][0]
    assert "压缩失败" in msg

    tree = await store.load("sid-c")
    assert tree is not None
    assert not any(isinstance(e, CompactionEntry) for e in tree.entries.values())


@pytest.mark.asyncio
async def test_compact_no_op_when_already_below_threshold() -> None:
    ctx, reply, store, _, _ = await _make_ctx(
        compact_result=CompactResult(
            ok=True, compacted=False, reason="within-budget", reason_code="below_threshold",
        )
    )

    await cmd_compact("", ctx)

    msg = reply.await_args[0][0]
    assert "无需压缩" in msg
    tree = await store.load("sid-c")
    assert tree is not None
    assert not any(isinstance(e, CompactionEntry) for e in tree.entries.values())
