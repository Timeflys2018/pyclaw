from __future__ import annotations

from pathlib import Path

import pytest

from pyclaw.storage.workspace.file import FileWorkspaceStore


@pytest.fixture
def store(tmp_path: Path) -> FileWorkspaceStore:
    return FileWorkspaceStore(base_dir=tmp_path)


@pytest.mark.asyncio
async def test_get_file_returns_none_when_missing(store: FileWorkspaceStore) -> None:
    result = await store.get_file("ws-1", "missing.txt")
    assert result is None


@pytest.mark.asyncio
async def test_put_then_get_roundtrip(store: FileWorkspaceStore) -> None:
    await store.put_file("ws-1", "hello.txt", "world")
    result = await store.get_file("ws-1", "hello.txt")
    assert result == "world"


@pytest.mark.asyncio
async def test_get_file_reads_existing_file(store: FileWorkspaceStore, tmp_path: Path) -> None:
    (tmp_path / "ws-2").mkdir()
    (tmp_path / "ws-2" / "data.txt").write_text("content", encoding="utf-8")
    result = await store.get_file("ws-2", "data.txt")
    assert result == "content"


@pytest.mark.asyncio
async def test_put_creates_parent_dirs(store: FileWorkspaceStore, tmp_path: Path) -> None:
    await store.put_file("new-ws", "nested/file.txt", "data")
    assert (tmp_path / "new-ws" / "nested" / "file.txt").read_text() == "data"
