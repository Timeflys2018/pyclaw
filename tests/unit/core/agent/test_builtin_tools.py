from __future__ import annotations

from pathlib import Path

import pytest

from pyclaw.core.agent.tools.builtin import BashTool, EditTool, ReadTool, WriteTool
from pyclaw.core.agent.tools.registry import ToolContext
from pyclaw.core.agent.tools.workspace import WorkspaceResolver
from pyclaw.models import WorkspaceConfig


def _ctx(workspace_path: Path) -> ToolContext:
    return ToolContext(workspace_id="default", workspace_path=workspace_path, session_id="s1")


def _resolver(tmp_path: Path) -> WorkspaceResolver:
    return WorkspaceResolver(WorkspaceConfig(workspaces={"default": str(tmp_path)}))


class TestBashTool:
    async def test_echo_succeeds(self, tmp_path: Path) -> None:
        result = await BashTool().execute(
            {"_call_id": "c1", "command": "echo hello"},
            _ctx(tmp_path),
        )
        assert not result.is_error
        assert "hello" in result.content[0].text

    async def test_nonzero_exit_returns_error(self, tmp_path: Path) -> None:
        result = await BashTool().execute(
            {"_call_id": "c1", "command": "exit 3"},
            _ctx(tmp_path),
        )
        assert result.is_error
        assert "exit_code=3" in result.content[0].text

    async def test_missing_command_returns_error(self, tmp_path: Path) -> None:
        result = await BashTool().execute({"_call_id": "c1"}, _ctx(tmp_path))
        assert result.is_error


class TestReadTool:
    async def test_reads_file(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("hello world")
        tool = ReadTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "file.txt"},
            _ctx(tmp_path),
        )
        assert not result.is_error
        assert result.content[0].text == "hello world"

    async def test_offset_limit_slices_lines(self, tmp_path: Path) -> None:
        (tmp_path / "file.txt").write_text("a\nb\nc\nd\ne\n")
        tool = ReadTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "file.txt", "offset": 2, "limit": 2},
            _ctx(tmp_path),
        )
        assert result.content[0].text == "b\nc\n"

    async def test_rejects_traversal(self, tmp_path: Path) -> None:
        tool = ReadTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "../outside.txt"},
            _ctx(tmp_path),
        )
        assert result.is_error

    async def test_missing_file(self, tmp_path: Path) -> None:
        tool = ReadTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "missing.txt"},
            _ctx(tmp_path),
        )
        assert result.is_error


class TestWriteTool:
    async def test_creates_file(self, tmp_path: Path) -> None:
        tool = WriteTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "out.txt", "content": "data"},
            _ctx(tmp_path),
        )
        assert not result.is_error
        assert (tmp_path / "out.txt").read_text() == "data"

    async def test_creates_parent_dirs(self, tmp_path: Path) -> None:
        tool = WriteTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "nested/deep/out.txt", "content": "x"},
            _ctx(tmp_path),
        )
        assert not result.is_error
        assert (tmp_path / "nested/deep/out.txt").read_text() == "x"

    async def test_rejects_traversal(self, tmp_path: Path) -> None:
        tool = WriteTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "../evil.txt", "content": "x"},
            _ctx(tmp_path),
        )
        assert result.is_error


class TestEditTool:
    async def test_replaces_unique_string(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("alpha beta gamma")
        tool = EditTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "f.txt", "old_string": "beta", "new_string": "BETA"},
            _ctx(tmp_path),
        )
        assert not result.is_error
        assert (tmp_path / "f.txt").read_text() == "alpha BETA gamma"

    async def test_multiple_matches_without_replace_all_errors(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("foo foo foo")
        tool = EditTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "f.txt", "old_string": "foo", "new_string": "bar"},
            _ctx(tmp_path),
        )
        assert result.is_error
        assert "3 times" in result.content[0].text

    async def test_replace_all(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("foo foo foo")
        tool = EditTool(_resolver(tmp_path))
        result = await tool.execute(
            {
                "_call_id": "c1",
                "path": "f.txt",
                "old_string": "foo",
                "new_string": "bar",
                "replace_all": True,
            },
            _ctx(tmp_path),
        )
        assert not result.is_error
        assert (tmp_path / "f.txt").read_text() == "bar bar bar"

    async def test_missing_old_string_errors(self, tmp_path: Path) -> None:
        (tmp_path / "f.txt").write_text("abc")
        tool = EditTool(_resolver(tmp_path))
        result = await tool.execute(
            {"_call_id": "c1", "path": "f.txt", "old_string": "xyz", "new_string": "..."},
            _ctx(tmp_path),
        )
        assert result.is_error
