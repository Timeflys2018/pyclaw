from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.commands.builtin import cmd_export
from pyclaw.core.commands.context import CommandContext
from pyclaw.models import (
    MessageEntry,
    SessionHeader,
    SessionTree,
    generate_entry_id,
    now_iso,
)
from pyclaw.storage.session.base import InMemorySessionStore


async def _make_ctx(workspace_path: Path, *, channel: str = "web") -> tuple[CommandContext, AsyncMock, InMemorySessionStore]:
    store = InMemorySessionStore()
    header = SessionHeader(id="sid-x", workspace_id="ws", agent_id="default", session_key="key")
    tree = SessionTree(header=header)
    user_id = generate_entry_id(set())
    user_entry = MessageEntry(
        id=user_id, parent_id=None, timestamp=now_iso(), role="user", content="Hello",
    )
    tree.entries[user_entry.id] = user_entry
    tree.order.append(user_entry.id)
    tree.leaf_id = user_entry.id
    await store.save_header(tree)
    await store.append_entry("sid-x", user_entry, leaf_id=user_entry.id)

    deps = MagicMock()
    deps.session_store = store

    reply = AsyncMock()
    ctx = CommandContext(
        session_id="sid-x",
        session_key="key",
        workspace_id="ws",
        user_id="me",
        channel=channel,
        deps=deps,
        session_router=SessionRouter(store=store),
        workspace_base=workspace_path.parent,
        reply=reply,
        dispatch_user_message=AsyncMock(),
        raw={"channel": channel, "tool_workspace_path": workspace_path},
    )
    return ctx, reply, store


@pytest.mark.asyncio
async def test_export_default_writes_markdown_file() -> None:
    workspace = Path(tempfile.mkdtemp())
    ctx, reply, _ = await _make_ctx(workspace)

    await cmd_export("", ctx)

    msg = reply.await_args[0][0]
    assert "✓" in msg

    exports = list((workspace / "exports").glob("session-*.md"))
    assert len(exports) == 1
    content = exports[0].read_text(encoding="utf-8")
    assert "# Session" in content
    assert "Hello" in content


@pytest.mark.asyncio
async def test_export_json_writes_json_file_loadable() -> None:
    workspace = Path(tempfile.mkdtemp())
    ctx, reply, _ = await _make_ctx(workspace)

    await cmd_export("json", ctx)

    msg = reply.await_args[0][0]
    assert "✓" in msg

    exports = list((workspace / "exports").glob("session-*.json"))
    assert len(exports) == 1
    parsed = json.loads(exports[0].read_text(encoding="utf-8"))
    assert parsed["header"]["id"] == "sid-x"


@pytest.mark.asyncio
async def test_export_filename_uses_random_hex_not_session_id() -> None:
    workspace = Path(tempfile.mkdtemp())
    ctx, _, _ = await _make_ctx(workspace)

    await cmd_export("", ctx)

    files = list((workspace / "exports").glob("*.md"))
    assert len(files) == 1
    name = files[0].name
    assert "sid-x" not in name
    assert re.match(r"^session-[0-9a-f]{8}-\d{8}T\d{6}Z\.md$", name), name


@pytest.mark.asyncio
async def test_export_inline_truncates_to_8192_utf8_bytes() -> None:
    workspace = Path(tempfile.mkdtemp())
    store = InMemorySessionStore()
    header = SessionHeader(id="sid-big", workspace_id="ws", agent_id="default", session_key="key")
    tree = SessionTree(header=header)
    eid = generate_entry_id(set())
    big_text = "工作" * 5000
    entry = MessageEntry(
        id=eid, parent_id=None, timestamp=now_iso(), role="user", content=big_text,
    )
    tree.entries[entry.id] = entry
    tree.order.append(entry.id)
    tree.leaf_id = entry.id
    await store.save_header(tree)
    await store.append_entry("sid-big", entry, leaf_id=entry.id)

    deps = MagicMock()
    deps.session_store = store

    reply = AsyncMock()
    ctx = CommandContext(
        session_id="sid-big",
        session_key="key",
        workspace_id="ws",
        user_id="me",
        channel="web",
        deps=deps,
        session_router=SessionRouter(store=store),
        workspace_base=workspace.parent,
        reply=reply,
        dispatch_user_message=AsyncMock(),
        raw={"channel": "web", "tool_workspace_path": workspace},
    )

    await cmd_export("inline", ctx)

    msg = reply.await_args[0][0]
    assert len(msg.encode("utf-8")) <= 8192 + len("\n\n…（内容已截断）".encode("utf-8")) + 4
    assert "（内容已截断）" in msg


@pytest.mark.asyncio
async def test_export_path_resolve_blocks_symlink_escape() -> None:
    base = Path(tempfile.mkdtemp())
    real_workspace = base / "real"
    real_workspace.mkdir()
    real_exports = real_workspace / "exports"
    real_exports.mkdir()

    bait = base / "evil"
    bait.mkdir()

    fake_workspace = base / "ws_link"
    fake_exports = fake_workspace / "exports"
    fake_workspace.mkdir()
    try:
        os.symlink(bait, fake_exports, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    ctx, reply, _ = await _make_ctx(fake_workspace)

    await cmd_export("", ctx)

    msg = reply.await_args[0][0]
    assert "⚠️" in msg or "✓" in msg

    bait_files = list(bait.glob("*.md"))
    if bait_files:
        assert "⚠️" in msg, "If file landed in bait dir, command MUST have warned"


@pytest.mark.asyncio
async def test_export_inline_does_not_write_file() -> None:
    workspace = Path(tempfile.mkdtemp())
    ctx, _, _ = await _make_ctx(workspace)

    await cmd_export("inline", ctx)

    exports_dir = workspace / "exports"
    if exports_dir.exists():
        files = list(exports_dir.glob("*"))
        assert len(files) == 0


@pytest.mark.asyncio
async def test_export_session_not_found_replies_error() -> None:
    workspace = Path(tempfile.mkdtemp())
    store = InMemorySessionStore()

    deps = MagicMock()
    deps.session_store = store

    reply = AsyncMock()
    ctx = CommandContext(
        session_id="nonexistent",
        session_key="key",
        workspace_id="ws",
        user_id="me",
        channel="web",
        deps=deps,
        session_router=SessionRouter(store=store),
        workspace_base=workspace.parent,
        reply=reply,
        dispatch_user_message=AsyncMock(),
        raw={"channel": "web", "tool_workspace_path": workspace},
    )

    await cmd_export("", ctx)

    msg = reply.await_args[0][0]
    assert "❌" in msg
    assert "不存在" in msg
