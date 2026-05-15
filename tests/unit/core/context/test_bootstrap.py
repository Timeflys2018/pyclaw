from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pyclaw.core.context.bootstrap import BOOTSTRAP_FILE_WARN_BYTES, load_bootstrap_context
from pyclaw.storage.workspace.file import FileWorkspaceStore


@pytest.fixture
def store(tmp_path: Path) -> FileWorkspaceStore:
    return FileWorkspaceStore(base_dir=tmp_path)


@pytest.mark.asyncio
async def test_no_files_returns_empty_string(store: FileWorkspaceStore) -> None:
    result = await load_bootstrap_context("ws-1", store, ["AGENTS.md", "SOUL.md"])
    assert result == ""


@pytest.mark.asyncio
async def test_single_file_no_header(store: FileWorkspaceStore) -> None:
    await store.put_file("ws-1", "AGENTS.md", "你是一个有用的助手。")
    result = await load_bootstrap_context("ws-1", store, ["AGENTS.md"])
    assert result == "你是一个有用的助手。"
    assert "## AGENTS.md" not in result


@pytest.mark.asyncio
async def test_multiple_files_with_headers(store: FileWorkspaceStore) -> None:
    await store.put_file("ws-1", "AGENTS.md", "agent content")
    await store.put_file("ws-1", "SOUL.md", "soul content")
    result = await load_bootstrap_context("ws-1", store, ["AGENTS.md", "SOUL.md"])
    assert "agent content" in result
    assert "## SOUL.md" in result
    assert result.startswith("agent content")


@pytest.mark.asyncio
async def test_missing_files_skipped(store: FileWorkspaceStore) -> None:
    await store.put_file("ws-1", "AGENTS.md", "only agents")
    result = await load_bootstrap_context("ws-1", store, ["AGENTS.md", "SOUL.md", "USER.md"])
    assert result == "only agents"
    assert "SOUL" not in result
    assert "USER" not in result


@pytest.mark.asyncio
async def test_custom_filenames(store: FileWorkspaceStore) -> None:
    await store.put_file("ws-1", "AGENTS.md", "agents")
    await store.put_file("ws-1", "SOUL.md", "soul")
    result = await load_bootstrap_context("ws-1", store, ["SOUL.md"])
    assert result == "soul"
    assert "agents" not in result


@pytest.mark.asyncio
async def test_warning_logged_for_large_content(store: FileWorkspaceStore) -> None:
    large_content = "x" * (BOOTSTRAP_FILE_WARN_BYTES + 100)
    await store.put_file("ws-1", "AGENTS.md", large_content)
    with patch("pyclaw.core.context.bootstrap.logger") as mock_logger:
        result = await load_bootstrap_context("ws-1", store, ["AGENTS.md"])
    mock_logger.warning.assert_called_once()
    assert result == large_content


@pytest.mark.asyncio
async def test_empty_content_files_skipped(store: FileWorkspaceStore) -> None:
    await store.put_file("ws-1", "AGENTS.md", "   ")
    await store.put_file("ws-1", "SOUL.md", "soul content")
    result = await load_bootstrap_context("ws-1", store, ["AGENTS.md", "SOUL.md"])
    assert result == "soul content"
