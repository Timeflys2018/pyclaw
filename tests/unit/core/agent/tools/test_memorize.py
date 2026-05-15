from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from pyclaw.core.agent.tools.memorize import MemorizeTool, _derive_session_key
from pyclaw.core.agent.tools.registry import ToolContext
from pyclaw.models import MessageEntry, now_iso
from pyclaw.storage.session.base import InMemorySessionStore


async def _build_session_with_tool_use(
    store: InMemorySessionStore, session_key: str = "feishu:cli_x:ou_abc"
) -> str:
    tree = await store.create_new_session(session_key, "default", "default")
    session_id = tree.header.id
    user_entry = MessageEntry(
        id="u1", parent_id=None, timestamp=now_iso(), role="user", content="do something"
    )
    await store.append_entry(session_id, user_entry, leaf_id="u1")
    assistant_entry = MessageEntry(
        id="a1",
        parent_id="u1",
        timestamp=now_iso(),
        role="assistant",
        content="calling tool",
        tool_calls=[{"id": "tc1", "function": {"name": "bash", "arguments": "{}"}}],
    )
    await store.append_entry(session_id, assistant_entry, leaf_id="a1")
    tool_entry = MessageEntry(
        id="t1", parent_id="a1", timestamp=now_iso(), role="tool", content="ok", tool_call_id="tc1"
    )
    await store.append_entry(session_id, tool_entry, leaf_id="t1")
    return session_id


async def _build_session_no_tool_use(
    store: InMemorySessionStore, session_key: str = "feishu:cli_x:ou_abc"
) -> str:
    tree = await store.create_new_session(session_key, "default", "default")
    session_id = tree.header.id
    user_entry = MessageEntry(
        id="u1", parent_id=None, timestamp=now_iso(), role="user", content="hello"
    )
    await store.append_entry(session_id, user_entry, leaf_id="u1")
    assistant_entry = MessageEntry(
        id="a1", parent_id="u1", timestamp=now_iso(), role="assistant", content="hi there"
    )
    await store.append_entry(session_id, assistant_entry, leaf_id="a1")
    return session_id


def _ctx(session_id: str) -> ToolContext:
    return ToolContext(workspace_id="default", workspace_path=Path("/tmp"), session_id=session_id)


# --- 5.5: Successful memorize writes to L2 ---


async def test_memorize_l2_success():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    session_id = await _build_session_with_tool_use(session_store)

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute(
        {"content": "user prefers dark mode", "layer": "L2", "type": "user_preference"},
        _ctx(session_id),
    )

    assert result.is_error is False
    assert "memorized" in result.content[0].text
    assert "L2" in result.content[0].text
    memory_store.store.assert_called_once()
    stored_entry = memory_store.store.call_args[0][1]
    assert stored_entry.layer == "L2"
    assert stored_entry.content == "user prefers dark mode"
    assert stored_entry.type == "user_preference"


# --- 5.6: Invalid layer returns is_error ---


@pytest.mark.parametrize("bad_layer", ["L1", "L4", "", None, "l2"])
async def test_memorize_invalid_layer(bad_layer):
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    session_id = await _build_session_with_tool_use(session_store)

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute({"content": "something", "layer": bad_layer}, _ctx(session_id))

    assert result.is_error is True
    assert "layer" in result.content[0].text


# --- 5.7: No tool_use in session returns is_error ---


async def test_memorize_no_tool_use_in_session():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    session_id = await _build_session_no_tool_use(session_store)

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute({"content": "remember this", "layer": "L2"}, _ctx(session_id))

    assert result.is_error is True
    assert "requires at least one successful tool execution" in result.content[0].text
    memory_store.store.assert_not_called()


# --- 5.8: Memorize with valid tool_call history succeeds (L3) ---


async def test_memorize_l3_with_full_history():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    session_id = await _build_session_with_tool_use(session_store)

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute(
        {"content": "always run tests before commit", "layer": "L3", "type": "workflow"},
        _ctx(session_id),
    )

    assert result.is_error is False
    assert "L3/workflow" in result.content[0].text
    stored_entry = memory_store.store.call_args[0][1]
    assert stored_entry.layer == "L3"
    assert stored_entry.type == "workflow"


# --- Additional: Empty content ---


async def test_memorize_empty_content():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    session_id = await _build_session_with_tool_use(session_store)

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute({"content": "", "layer": "L2"}, _ctx(session_id))

    assert result.is_error is True
    assert "non-empty string" in result.content[0].text


async def test_memorize_whitespace_only_content():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    session_id = await _build_session_with_tool_use(session_store)

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute({"content": "   ", "layer": "L2"}, _ctx(session_id))

    assert result.is_error is True
    assert "non-empty string" in result.content[0].text


# --- Additional: Non-string content ---


async def test_memorize_non_string_content():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    session_id = await _build_session_with_tool_use(session_store)

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute({"content": 123, "layer": "L2"}, _ctx(session_id))

    assert result.is_error is True
    assert "non-empty string" in result.content[0].text


# --- Additional: Session not found ---


async def test_memorize_session_not_found():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute({"content": "test", "layer": "L2"}, _ctx("nonexistent:s:abc123"))

    assert result.is_error is True
    assert "session not found" in result.content[0].text


# --- Additional: memory_store.store raises ---


async def test_memorize_store_raises():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    memory_store.store.side_effect = RuntimeError("connection lost")
    session_id = await _build_session_with_tool_use(session_store)

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute({"content": "something", "layer": "L2"}, _ctx(session_id))

    assert result.is_error is True
    assert "store failed" in result.content[0].text
    assert "connection lost" in result.content[0].text


# --- Additional: _derive_session_key ---


def test_derive_session_key_with_s_separator():
    assert _derive_session_key("feishu:cli_x:s:abc123") == "feishu:cli_x"


def test_derive_session_key_without_s_separator():
    assert _derive_session_key("simple_session_id") == "simple_session_id"


def test_derive_session_key_multiple_s_separators():
    assert _derive_session_key("feishu:a:s:first:s:second") == "feishu:a"


# --- Additional: default type is "general" ---


async def test_memorize_default_type_is_general():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    session_id = await _build_session_with_tool_use(session_store)

    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute({"content": "some fact", "layer": "L2"}, _ctx(session_id))

    assert result.is_error is False
    stored_entry = memory_store.store.call_args[0][1]
    assert stored_entry.type == "general"


# --- Additional: long content is truncated in output ---


async def test_memorize_long_content_truncated_in_output():
    session_store = InMemorySessionStore()
    memory_store = AsyncMock()
    session_id = await _build_session_with_tool_use(session_store)

    long_content = "x" * 200
    tool = MemorizeTool(memory_store=memory_store, session_store=session_store)
    result = await tool.execute({"content": long_content, "layer": "L2"}, _ctx(session_id))

    assert result.is_error is False
    assert result.content[0].text.endswith("...")
    stored_entry = memory_store.store.call_args[0][1]
    assert stored_entry.content == long_content
