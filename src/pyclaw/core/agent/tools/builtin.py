from __future__ import annotations

import asyncio
import signal
from pathlib import Path
from typing import Any

from pyclaw.core.agent.tools.registry import ToolContext, error_result, text_result
from pyclaw.core.agent.tools.workspace import WorkspaceBoundaryError, WorkspaceResolver
from pyclaw.models import ToolResult

BASH_DEFAULT_TIMEOUT_SECONDS = 120.0
READ_WRITE_DEFAULT_TIMEOUT_SECONDS = 30.0
BASH_ABORT_GRACE_SECONDS = 2.0


class BashTool:
    name = "bash"
    description = "Execute a shell command in the workspace. Returns stdout and stderr."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "Shell command to execute"},
            "timeout_seconds": {
                "type": "number",
                "description": "Timeout in seconds (default 120)",
            },
        },
        "required": ["command"],
    }
    side_effect = True
    tool_class = "write"

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        command = args.get("command")
        if not isinstance(command, str) or not command.strip():
            return error_result(call_id, "bash: 'command' is required")

        if context.abort.is_set():
            return error_result(call_id, "bash: aborted before spawn")

        timeout = float(args.get("timeout_seconds") or BASH_DEFAULT_TIMEOUT_SECONDS)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(context.workspace_path),
            )
        except OSError as exc:
            return error_result(call_id, f"bash: failed to spawn: {exc}")

        comm_task = asyncio.ensure_future(proc.communicate())
        abort_task = asyncio.ensure_future(context.abort.wait())

        try:
            done, _pending = await asyncio.wait(
                [comm_task, abort_task],
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        except asyncio.CancelledError:
            _terminate_proc(proc)
            comm_task.cancel()
            abort_task.cancel()
            raise

        if comm_task in done:
            abort_task.cancel()
            stdout_b, stderr_b = comm_task.result()
        elif abort_task in done:
            comm_task.cancel()
            await _abort_proc(proc, BASH_ABORT_GRACE_SECONDS)
            return error_result(call_id, "bash: aborted")
        else:
            abort_task.cancel()
            comm_task.cancel()
            await _abort_proc(proc, BASH_ABORT_GRACE_SECONDS)
            return error_result(call_id, f"bash: command timed out after {timeout}s")

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        exit_code = proc.returncode or 0

        parts = []
        if stdout:
            parts.append(f"[stdout]\n{stdout}")
        if stderr:
            parts.append(f"[stderr]\n{stderr}")
        parts.append(f"[exit_code={exit_code}]")
        body = "\n".join(parts)

        if exit_code != 0:
            return error_result(call_id, body)
        return text_result(call_id, body)


def _terminate_proc(proc: asyncio.subprocess.Process) -> None:
    try:
        proc.kill()
    except ProcessLookupError:
        pass


async def _abort_proc(proc: asyncio.subprocess.Process, grace_s: float) -> None:
    if proc.returncode is not None:
        return
    try:
        proc.send_signal(signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_s)
    except TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=grace_s)
        except TimeoutError:
            pass


class ReadTool:
    name = "read"
    description = "Read a file within the workspace. Supports optional line offset and limit."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to workspace"},
            "offset": {"type": "integer", "description": "1-based starting line (optional)"},
            "limit": {"type": "integer", "description": "Max number of lines (optional)"},
        },
        "required": ["path"],
    }
    side_effect = False
    tool_class = "read"

    def __init__(self, resolver: WorkspaceResolver) -> None:
        self._resolver = resolver

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            return error_result(call_id, "read: 'path' is required")

        if context.abort.is_set():
            return error_result(call_id, "read: aborted")

        try:
            full_path = self._resolver.resolve_within(context.workspace_path, raw_path)
        except WorkspaceBoundaryError as exc:
            return error_result(call_id, str(exc))

        if not full_path.exists():
            return error_result(call_id, f"read: file not found: {raw_path}")
        if full_path.is_dir():
            return error_result(call_id, f"read: {raw_path} is a directory")

        try:
            content = await asyncio.to_thread(
                full_path.read_text, encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            return error_result(call_id, f"read: {exc}")

        offset = args.get("offset")
        limit = args.get("limit")
        if isinstance(offset, int) or isinstance(limit, int):
            lines = content.splitlines(keepends=True)
            start = max(0, (offset - 1) if isinstance(offset, int) and offset > 0 else 0)
            end = start + limit if isinstance(limit, int) and limit > 0 else len(lines)
            content = "".join(lines[start:end])

        return text_result(call_id, content)


class WriteTool:
    name = "write"
    description = "Write (create or overwrite) a file within the workspace."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to workspace"},
            "content": {"type": "string", "description": "File content to write"},
        },
        "required": ["path", "content"],
    }
    side_effect = True
    tool_class = "write"

    def __init__(self, resolver: WorkspaceResolver) -> None:
        self._resolver = resolver

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        raw_path = args.get("path")
        content = args.get("content")
        if not isinstance(raw_path, str) or not raw_path:
            return error_result(call_id, "write: 'path' is required")
        if not isinstance(content, str):
            return error_result(call_id, "write: 'content' must be a string")

        if context.abort.is_set():
            return error_result(call_id, "write: aborted")

        try:
            full_path = self._resolver.resolve_within(context.workspace_path, raw_path)
        except WorkspaceBoundaryError as exc:
            return error_result(call_id, str(exc))

        try:
            await asyncio.to_thread(_write_file, full_path, content)
        except OSError as exc:
            return error_result(call_id, f"write: {exc}")

        return text_result(call_id, f"wrote {len(content)} bytes to {raw_path}")


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class EditTool:
    name = "edit"
    description = "Replace a unique old_string with new_string in a file. Use replace_all for multiple matches."
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to workspace"},
            "old_string": {"type": "string", "description": "Exact text to replace"},
            "new_string": {"type": "string", "description": "Replacement text"},
            "replace_all": {"type": "boolean", "description": "Replace all occurrences"},
        },
        "required": ["path", "old_string", "new_string"],
    }
    side_effect = True
    tool_class = "write"

    def __init__(self, resolver: WorkspaceResolver) -> None:
        self._resolver = resolver

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        raw_path = args.get("path")
        old = args.get("old_string")
        new = args.get("new_string")
        replace_all = bool(args.get("replace_all", False))

        if not isinstance(raw_path, str) or not raw_path:
            return error_result(call_id, "edit: 'path' is required")
        if not isinstance(old, str) or not isinstance(new, str):
            return error_result(call_id, "edit: 'old_string' and 'new_string' must be strings")

        if context.abort.is_set():
            return error_result(call_id, "edit: aborted")

        try:
            full_path = self._resolver.resolve_within(context.workspace_path, raw_path)
        except WorkspaceBoundaryError as exc:
            return error_result(call_id, str(exc))

        if not full_path.exists():
            return error_result(call_id, f"edit: file not found: {raw_path}")

        try:
            content = await asyncio.to_thread(
                full_path.read_text, encoding="utf-8", errors="replace"
            )
        except OSError as exc:
            return error_result(call_id, f"edit: {exc}")

        count = content.count(old)
        if count == 0:
            return error_result(call_id, f"edit: old_string not found in {raw_path}")
        if count > 1 and not replace_all:
            return error_result(
                call_id,
                f"edit: old_string found {count} times in {raw_path}; set replace_all=true or provide a unique snippet",
            )

        updated = content.replace(old, new) if replace_all else content.replace(old, new, 1)
        try:
            await asyncio.to_thread(full_path.write_text, updated, encoding="utf-8")
        except OSError as exc:
            return error_result(call_id, f"edit: {exc}")

        return text_result(
            call_id, f"edited {raw_path} ({count if replace_all else 1} replacement(s))"
        )


def register_builtin_tools(registry, resolver: WorkspaceResolver) -> None:
    registry.register(BashTool())
    registry.register(ReadTool(resolver))
    registry.register(WriteTool(resolver))
    registry.register(EditTool(resolver))
