"""Unit tests for ForgetTool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.core.agent.tools.forget import ForgetTool
from pyclaw.core.agent.tools.registry import ToolContext


@pytest.fixture
def tool():
    memory_store = AsyncMock()
    session_store = AsyncMock()
    return ForgetTool(memory_store, session_store)


@pytest.fixture
def context():
    return ToolContext(
        workspace_id="test_ws",
        workspace_path=Path("/tmp/test"),
        session_id="web:admin:s:abc123",
    )


def _make_args(entry_id: str = "abcd1234", reason: str = "已过时", call_id: str = "call_1") -> dict:
    return {"entry_id": entry_id, "reason": reason, "_call_id": call_id}


class TestForgetToolSuccess:
    """Success: valid entry_id + reason → archive succeeds → confirmation."""

    @pytest.mark.asyncio
    async def test_archive_success(self, tool: ForgetTool, context: ToolContext):
        mock_conn = MagicMock()
        mock_conn.execute.return_value = [("full-uuid-abcd1234-rest", "Some procedure content")]
        tool._memory_store._sqlite = MagicMock()
        tool._memory_store._sqlite._get_conn = AsyncMock(return_value=mock_conn)
        tool._memory_store.archive_entry = AsyncMock(return_value=True)

        mock_tree = MagicMock()
        tool._session_store.load = AsyncMock(return_value=mock_tree)

        with patch.object(ForgetTool, "_has_non_error_tool_use", return_value=True):
            result = await tool.execute(_make_args(), context)

        assert not result.is_error
        assert "已归档" in result.content[0].text
        assert "已过时" in result.content[0].text


class TestForgetToolNoMatch:
    """No match: entry_id doesn't match any active procedure."""

    @pytest.mark.asyncio
    async def test_no_match(self, tool: ForgetTool, context: ToolContext):
        mock_conn = MagicMock()
        mock_conn.execute.return_value = []
        tool._memory_store._sqlite = MagicMock()
        tool._memory_store._sqlite._get_conn = AsyncMock(return_value=mock_conn)

        mock_tree = MagicMock()
        tool._session_store.load = AsyncMock(return_value=mock_tree)

        with patch.object(ForgetTool, "_has_non_error_tool_use", return_value=True):
            result = await tool.execute(_make_args(entry_id="nonexist"), context)

        assert result.is_error
        assert "未找到" in result.content[0].text


class TestForgetToolMultipleMatch:
    """Multiple match: short entry_id matches >1 procedure."""

    @pytest.mark.asyncio
    async def test_multiple_match(self, tool: ForgetTool, context: ToolContext):
        mock_conn = MagicMock()
        mock_conn.execute.return_value = [
            ("id-aaa-111", "content1"),
            ("id-aaa-222", "content2"),
        ]
        tool._memory_store._sqlite = MagicMock()
        tool._memory_store._sqlite._get_conn = AsyncMock(return_value=mock_conn)

        mock_tree = MagicMock()
        tool._session_store.load = AsyncMock(return_value=mock_tree)

        with patch.object(ForgetTool, "_has_non_error_tool_use", return_value=True):
            result = await tool.execute(_make_args(entry_id="id-aaa"), context)

        assert result.is_error
        assert "匹配多条" in result.content[0].text
        assert "2" in result.content[0].text


class TestForgetToolGuardReject:
    """Guard reject: no prior tool use in session."""

    @pytest.mark.asyncio
    async def test_guard_no_tool_use(self, tool: ForgetTool, context: ToolContext):
        mock_tree = MagicMock()
        tool._session_store.load = AsyncMock(return_value=mock_tree)

        with patch.object(ForgetTool, "_has_non_error_tool_use", return_value=False):
            result = await tool.execute(_make_args(), context)

        assert result.is_error
        assert "需要先" in result.content[0].text

    @pytest.mark.asyncio
    async def test_guard_no_session(self, tool: ForgetTool, context: ToolContext):
        """session_store.load returns None → error."""
        tool._session_store.load = AsyncMock(return_value=None)

        result = await tool.execute(_make_args(), context)

        assert result.is_error
        assert "需要先" in result.content[0].text


class TestForgetToolAlreadyArchived:
    """Already archived: archive_entry returns False."""

    @pytest.mark.asyncio
    async def test_already_archived(self, tool: ForgetTool, context: ToolContext):
        mock_conn = MagicMock()
        mock_conn.execute.return_value = [("full-uuid-here", "Some content")]
        tool._memory_store._sqlite = MagicMock()
        tool._memory_store._sqlite._get_conn = AsyncMock(return_value=mock_conn)
        tool._memory_store.archive_entry = AsyncMock(return_value=False)

        mock_tree = MagicMock()
        tool._session_store.load = AsyncMock(return_value=mock_tree)

        with patch.object(ForgetTool, "_has_non_error_tool_use", return_value=True):
            result = await tool.execute(_make_args(), context)

        assert result.is_error
        assert "已处于归档状态" in result.content[0].text


class TestForgetToolMissingFields:
    """Missing entry_id or reason → error."""

    @pytest.mark.asyncio
    async def test_missing_entry_id(self, tool: ForgetTool, context: ToolContext):
        result = await tool.execute(_make_args(entry_id=""), context)

        assert result.is_error
        assert "entry_id" in result.content[0].text

    @pytest.mark.asyncio
    async def test_missing_reason(self, tool: ForgetTool, context: ToolContext):
        result = await tool.execute(_make_args(reason=""), context)

        assert result.is_error
        assert "reason" in result.content[0].text


class TestForgetGraduatedExcluded:
    """Graduated entries should not be found by ForgetTool (SQL filters status='active')."""

    @pytest.mark.asyncio
    async def test_graduated_entry_invisible(self, tool: ForgetTool, context: ToolContext):
        mock_conn = MagicMock()
        mock_conn.execute.return_value = []
        tool._memory_store._sqlite = MagicMock()
        tool._memory_store._sqlite._get_conn = AsyncMock(return_value=mock_conn)

        mock_tree = MagicMock()
        tool._session_store.load = AsyncMock(return_value=mock_tree)

        with patch.object(ForgetTool, "_has_non_error_tool_use", return_value=True):
            result = await tool.execute(
                _make_args(entry_id="graduated_id", reason="test graduated"),
                context,
            )

        assert result.is_error
        assert "未找到" in result.content[0].text
