from __future__ import annotations

from pathlib import Path

import pytest

from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.storage.workspace.file import FileWorkspaceStore


@pytest.fixture
def store(tmp_path: Path) -> FileWorkspaceStore:
    return FileWorkspaceStore(base_dir=tmp_path)


@pytest.mark.asyncio
async def test_get_bootstrap_none_without_workspace_store() -> None:
    engine = DefaultContextEngine()
    assert await engine.get_bootstrap("sess-1") is None


@pytest.mark.asyncio
async def test_get_bootstrap_returns_agents_md(
    store: FileWorkspaceStore,
) -> None:
    workspace_id = "feishu_cli_x_ou_abc"
    await store.put_file(workspace_id, "AGENTS.md", "你是一个专业的代码助手。")
    engine = DefaultContextEngine(workspace_store=store)
    session_id = "feishu:cli_x:ou_abc"
    assert await engine.get_bootstrap(session_id) == "你是一个专业的代码助手。"


@pytest.mark.asyncio
async def test_get_bootstrap_returns_none_when_no_files(
    store: FileWorkspaceStore,
) -> None:
    engine = DefaultContextEngine(workspace_store=store)
    assert await engine.get_bootstrap("feishu:cli_x:ou_abc") is None


@pytest.mark.asyncio
async def test_get_bootstrap_caches_per_workspace(
    store: FileWorkspaceStore,
) -> None:
    workspace_id = "feishu_cli_x_ou_cache"
    await store.put_file(workspace_id, "AGENTS.md", "initial content")
    engine = DefaultContextEngine(workspace_store=store)
    session_id = "feishu:cli_x:ou_cache"

    result1 = await engine.get_bootstrap(session_id)
    assert result1 == "initial content"

    await store.put_file(workspace_id, "AGENTS.md", "updated content")
    result2 = await engine.get_bootstrap(session_id)
    assert result2 == "initial content"


@pytest.mark.asyncio
async def test_get_bootstrap_different_sessions_independent(
    store: FileWorkspaceStore,
) -> None:
    await store.put_file("feishu_cli_x_ou_A", "AGENTS.md", "session A content")
    await store.put_file("feishu_cli_x_ou_B", "AGENTS.md", "session B content")
    engine = DefaultContextEngine(workspace_store=store)

    result_a = await engine.get_bootstrap("feishu:cli_x:ou_A")
    result_b = await engine.get_bootstrap("feishu:cli_x:ou_B")

    assert result_a == "session A content"
    assert result_b == "session B content"


@pytest.mark.asyncio
async def test_get_bootstrap_caches_by_workspace_id_not_session_id(
    store: FileWorkspaceStore,
) -> None:
    await store.put_file("feishu_cli_x_ou_abc", "AGENTS.md", "shared workspace content")
    engine = DefaultContextEngine(workspace_store=store)

    session_old = "feishu:cli_x:ou_abc:s:old12345"
    session_new = "feishu:cli_x:ou_abc:s:new67890"

    result1 = await engine.get_bootstrap(session_old)
    assert result1 == "shared workspace content"

    result2 = await engine.get_bootstrap(session_new)
    assert result2 == "shared workspace content"

    assert engine._bootstrap_cache.get("feishu_cli_x_ou_abc") == "shared workspace content"
    assert len(engine._bootstrap_cache) == 1


@pytest.mark.asyncio
async def test_get_bootstrap_empty_files_list(
    store: FileWorkspaceStore,
) -> None:
    await store.put_file("feishu_cli_x_ou_abc", "AGENTS.md", "should not be read")
    engine = DefaultContextEngine(workspace_store=store, bootstrap_files=[])
    assert await engine.get_bootstrap("feishu:cli_x:ou_abc") is None


@pytest.mark.asyncio
async def test_assemble_without_memory_store_returns_none_addition() -> None:
    engine = DefaultContextEngine()
    messages = [{"role": "user", "content": "hello"}]
    result = await engine.assemble(session_id="sess-1", messages=messages)
    assert result.messages == messages
    assert result.system_prompt_addition is None


@pytest.mark.asyncio
async def test_assemble_bootstrap_no_longer_in_system_prompt_addition(
    store: FileWorkspaceStore,
) -> None:
    workspace_id = "feishu_cli_x_ou_abc"
    await store.put_file(workspace_id, "AGENTS.md", "bootstrap content")
    engine = DefaultContextEngine(workspace_store=store)
    session_id = "feishu:cli_x:ou_abc"
    result = await engine.assemble(session_id=session_id, messages=[])
    assert result.system_prompt_addition is None
