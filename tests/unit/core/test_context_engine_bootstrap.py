from __future__ import annotations

from pathlib import Path

import pytest

from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.storage.workspace.file import FileWorkspaceStore


@pytest.fixture
def store(tmp_path: Path) -> FileWorkspaceStore:
    return FileWorkspaceStore(base_dir=tmp_path)


@pytest.mark.asyncio
async def test_assemble_passthrough_without_workspace_store() -> None:
    engine = DefaultContextEngine()
    messages = [{"role": "user", "content": "hello"}]
    result = await engine.assemble(session_id="sess-1", messages=messages)
    assert result.messages == messages
    assert result.system_prompt_addition is None


@pytest.mark.asyncio
async def test_assemble_injects_agents_md_via_system_prompt_addition(
    store: FileWorkspaceStore,
) -> None:
    workspace_id = "feishu_cli_x_ou_abc"
    await store.put_file(workspace_id, "AGENTS.md", "你是一个专业的代码助手。")
    engine = DefaultContextEngine(workspace_store=store)
    session_id = "feishu:cli_x:ou_abc"
    messages = [{"role": "user", "content": "hello"}]
    result = await engine.assemble(session_id=session_id, messages=messages)
    assert result.system_prompt_addition == "你是一个专业的代码助手。"
    assert result.messages == messages


@pytest.mark.asyncio
async def test_assemble_returns_none_addition_when_no_files(
    store: FileWorkspaceStore,
) -> None:
    engine = DefaultContextEngine(workspace_store=store)
    result = await engine.assemble(session_id="feishu:cli_x:ou_abc", messages=[])
    assert result.system_prompt_addition is None


@pytest.mark.asyncio
async def test_assemble_caches_bootstrap_per_session(
    store: FileWorkspaceStore,
) -> None:
    workspace_id = "feishu_cli_x_ou_cache"
    await store.put_file(workspace_id, "AGENTS.md", "initial content")
    engine = DefaultContextEngine(workspace_store=store)
    session_id = "feishu:cli_x:ou_cache"

    result1 = await engine.assemble(session_id=session_id, messages=[])
    assert result1.system_prompt_addition == "initial content"

    await store.put_file(workspace_id, "AGENTS.md", "updated content")
    result2 = await engine.assemble(session_id=session_id, messages=[])
    assert result2.system_prompt_addition == "initial content"


@pytest.mark.asyncio
async def test_assemble_different_sessions_independent_cache(
    store: FileWorkspaceStore,
) -> None:
    await store.put_file("feishu_cli_x_ou_A", "AGENTS.md", "session A content")
    await store.put_file("feishu_cli_x_ou_B", "AGENTS.md", "session B content")
    engine = DefaultContextEngine(workspace_store=store)

    result_a = await engine.assemble(session_id="feishu:cli_x:ou_A", messages=[])
    result_b = await engine.assemble(session_id="feishu:cli_x:ou_B", messages=[])

    assert result_a.system_prompt_addition == "session A content"
    assert result_b.system_prompt_addition == "session B content"


@pytest.mark.asyncio
async def test_assemble_caches_by_workspace_id_not_session_id(
    store: FileWorkspaceStore,
) -> None:
    await store.put_file("feishu_cli_x_ou_abc", "AGENTS.md", "shared workspace content")
    engine = DefaultContextEngine(workspace_store=store)

    session_old = "feishu:cli_x:ou_abc:s:old12345"
    session_new = "feishu:cli_x:ou_abc:s:new67890"

    result1 = await engine.assemble(session_id=session_old, messages=[])
    assert result1.system_prompt_addition == "shared workspace content"

    result2 = await engine.assemble(session_id=session_new, messages=[])
    assert result2.system_prompt_addition == "shared workspace content"

    assert engine._bootstrap_cache.get("feishu_cli_x_ou_abc") == "shared workspace content"
    assert len(engine._bootstrap_cache) == 1


@pytest.mark.asyncio
async def test_assemble_empty_bootstrap_files_list(
    store: FileWorkspaceStore,
) -> None:
    await store.put_file("feishu_cli_x_ou_abc", "AGENTS.md", "should not be read")
    engine = DefaultContextEngine(workspace_store=store, bootstrap_files=[])
    result = await engine.assemble(session_id="feishu:cli_x:ou_abc", messages=[])
    assert result.system_prompt_addition is None
