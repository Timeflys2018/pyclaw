from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
import mcp.types as mcp_types
import pytest
from mcp.shared.exceptions import McpError

from pyclaw.integrations.mcp.adapter import (
    MCPToolAdapter,
    _convert_content_block,
    _derive_tool_class,
)
from pyclaw.integrations.mcp.errors import MCPServerDeadError
from pyclaw.integrations.mcp.settings import McpServerConfig


def _make_remote_tool(name="read_file", read_only=None) -> mcp_types.Tool:
    annotations = None
    if read_only is not None:
        annotations = mcp_types.ToolAnnotations(readOnlyHint=read_only)
    return mcp_types.Tool(
        name=name,
        description=f"Tool {name}",
        inputSchema={"type": "object", "properties": {"path": {"type": "string"}}},
        annotations=annotations,
    )


def _make_config(**kwargs) -> McpServerConfig:
    return McpServerConfig(command="npx", **kwargs)


def _make_text_call_result(text="ok", is_error=False) -> mcp_types.CallToolResult:
    return mcp_types.CallToolResult(
        content=[mcp_types.TextContent(type="text", text=text)],
        isError=is_error,
    )


class TestDeriveToolClass:
    def test_forced_tool_class_wins(self):
        cfg = _make_config(forced_tool_class="read")
        ann = mcp_types.ToolAnnotations(readOnlyHint=False)
        assert _derive_tool_class(cfg, ann) == "read"

    def test_trusted_read_only_hint_true(self):
        cfg = _make_config(trust_annotations=True)
        ann = mcp_types.ToolAnnotations(readOnlyHint=True)
        assert _derive_tool_class(cfg, ann) == "read"

    def test_trusted_no_annotations_default_write(self):
        cfg = _make_config(trust_annotations=True)
        assert _derive_tool_class(cfg, None) == "write"

    def test_untrusted_overrides_annotations(self):
        cfg = _make_config(trust_annotations=False)
        ann = mcp_types.ToolAnnotations(readOnlyHint=True)
        assert _derive_tool_class(cfg, ann) == "write"

    def test_trust_with_read_only_hint_false(self):
        cfg = _make_config(trust_annotations=True)
        ann = mcp_types.ToolAnnotations(readOnlyHint=False)
        assert _derive_tool_class(cfg, ann) == "write"


class TestContentBlockConversion:
    def test_text_content(self):
        block = _convert_content_block(mcp_types.TextContent(type="text", text="hello"))
        assert block.type == "text"
        assert block.text == "hello"

    def test_image_content(self):
        block = _convert_content_block(
            mcp_types.ImageContent(type="image", data="base64data", mimeType="image/png")
        )
        assert block.type == "image"
        assert block.data == "base64data"
        assert block.mime_type == "image/png"

    def test_audio_content_falls_back_to_text(self):
        block = _convert_content_block(
            mcp_types.AudioContent(type="audio", data="aabbcc", mimeType="audio/wav")
        )
        assert block.type == "text"
        assert "audio" in block.text
        assert "audio/wav" in block.text


class TestMCPToolAdapter:
    def _make_adapter(self, **overrides) -> MCPToolAdapter:
        defaults = dict(
            server_name="filesystem",
            remote_tool=_make_remote_tool("read_file", read_only=True),
            server_config=_make_config(),
            group=MagicMock(),
            sdk_key="read_file",
        )
        defaults.update(overrides)
        return MCPToolAdapter(**defaults)

    def test_canonical_name(self):
        adapter = self._make_adapter()
        assert adapter.name == "filesystem:read_file"
        assert adapter._sdk_key == "read_file"
        assert adapter.server_name == "filesystem"

    def test_tool_class_read_when_read_only_hint_true_trusted(self):
        adapter = self._make_adapter()
        assert adapter.tool_class == "read"
        assert adapter.side_effect is False

    def test_tool_class_write_when_no_hint(self):
        adapter = self._make_adapter(remote_tool=_make_remote_tool("write_file", read_only=None))
        assert adapter.tool_class == "write"
        assert adapter.side_effect is True

    def test_reject_remote_name_with_colon(self):
        with pytest.raises(ValueError, match="must not contain ':'"):
            MCPToolAdapter(
                server_name="bad",
                remote_tool=_make_remote_tool(name="get:user"),
                server_config=_make_config(),
                group=MagicMock(),
                sdk_key="get:user",
            )

    def test_parameters_passthrough(self):
        adapter = self._make_adapter()
        assert adapter.parameters["type"] == "object"
        assert "path" in adapter.parameters["properties"]

    @pytest.mark.asyncio
    async def test_execute_uses_sdk_key_not_canonical_name(self):
        group = MagicMock()
        group.call_tool = AsyncMock(return_value=_make_text_call_result("contents"))
        adapter = self._make_adapter(group=group, sdk_key="read_file")

        result = await adapter.execute({"path": "/tmp"}, MagicMock(tool_call_id="c1"))

        group.call_tool.assert_awaited_once_with("read_file", {"path": "/tmp"})
        assert result.is_error is False
        assert result.content[0].text == "contents"

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        group = MagicMock()

        async def slow(*args, **kwargs):
            await asyncio.sleep(10)

        group.call_tool = slow
        cfg = _make_config(call_timeout_seconds=0.05)
        adapter = self._make_adapter(server_config=cfg, group=group)

        result = await adapter.execute({}, MagicMock(tool_call_id="c1"))
        assert result.is_error is True
        assert "timed out" in result.content[0].text

    @pytest.mark.asyncio
    async def test_execute_dead_server_anyio_closed(self):
        group = MagicMock()
        group.call_tool = AsyncMock(side_effect=anyio.ClosedResourceError())
        adapter = self._make_adapter(group=group)

        with pytest.raises(MCPServerDeadError) as exc:
            await adapter.execute({}, MagicMock(tool_call_id="c1"))
        assert exc.value.server_name == "filesystem"

    @pytest.mark.asyncio
    async def test_execute_dead_server_broken_pipe(self):
        import errno
        group = MagicMock()
        group.call_tool = AsyncMock(side_effect=OSError(errno.EPIPE, "broken pipe"))
        adapter = self._make_adapter(group=group)

        with pytest.raises(MCPServerDeadError) as exc:
            await adapter.execute({}, MagicMock(tool_call_id="c1"))
        assert exc.value.server_name == "filesystem"

    @pytest.mark.asyncio
    async def test_execute_oserror_not_broken_pipe_returns_error(self):
        group = MagicMock()
        group.call_tool = AsyncMock(side_effect=OSError(13, "permission denied"))
        adapter = self._make_adapter(group=group)

        result = await adapter.execute({}, MagicMock(tool_call_id="c1"))
        assert result.is_error is True
        assert "OSError" in result.content[0].text

    @pytest.mark.asyncio
    async def test_execute_mcp_error_with_connection_loss_code(self):
        from mcp.shared.exceptions import McpError
        group = MagicMock()
        err = McpError(mcp_types.ErrorData(code=-32000, message="connection lost"))
        group.call_tool = AsyncMock(side_effect=err)
        adapter = self._make_adapter(group=group)

        with pytest.raises(MCPServerDeadError):
            await adapter.execute({}, MagicMock(tool_call_id="c1"))

    @pytest.mark.asyncio
    async def test_execute_mcp_error_with_other_code_returns_error(self):
        from mcp.shared.exceptions import McpError
        group = MagicMock()
        err = McpError(mcp_types.ErrorData(code=-32602, message="invalid params"))
        group.call_tool = AsyncMock(side_effect=err)
        adapter = self._make_adapter(group=group)

        result = await adapter.execute({}, MagicMock(tool_call_id="c1"))
        assert result.is_error is True
        assert "invalid params" in result.content[0].text

    @pytest.mark.asyncio
    async def test_execute_returns_is_error_from_result(self):
        group = MagicMock()
        group.call_tool = AsyncMock(return_value=_make_text_call_result("nope", is_error=True))
        adapter = self._make_adapter(group=group)

        result = await adapter.execute({}, MagicMock(tool_call_id="c1"))
        assert result.is_error is True
        assert result.content[0].text == "nope"
