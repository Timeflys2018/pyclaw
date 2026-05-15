from __future__ import annotations

import time
import uuid
from typing import Any

from pyclaw.core.agent.tools.registry import ToolContext, error_result, text_result
from pyclaw.models import ToolResult
from pyclaw.storage.memory.base import MemoryEntry, MemoryStore
from pyclaw.storage.protocols import SessionStore


class MemorizeTool:
    name = "memorize"
    description = (
        "Persist a durable memory for future sessions. Use layer='L2' for factual knowledge "
        "(user preferences, environment facts) and layer='L3' for workflow SOPs. "
        "Can only be called after you have executed at least one tool in the current session."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "content": {
                "type": "string",
                "description": "The fact, preference, or workflow to remember",
            },
            "layer": {
                "type": "string",
                "enum": ["L2", "L3"],
                "description": "L2 = factual knowledge, L3 = workflow SOP",
            },
            "type": {
                "type": "string",
                "description": "Category: user_preference, env_fact, workflow, etc.",
            },
        },
        "required": ["content", "layer"],
    }
    side_effect = True
    tool_class = "memory-write-safe"

    def __init__(self, memory_store: MemoryStore, session_store: SessionStore) -> None:
        self._memory_store = memory_store
        self._session_store = session_store

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        content = args.get("content")
        layer = args.get("layer")
        entry_type = args.get("type", "general")

        if not isinstance(content, str) or not content.strip():
            return error_result(call_id, "memorize: 'content' must be a non-empty string")

        if layer not in ("L2", "L3"):
            return error_result(call_id, f"memorize: 'layer' must be 'L2' or 'L3', got {layer!r}")

        try:
            tree = await self._session_store.load(context.session_id)
        except Exception as exc:
            return error_result(call_id, f"memorize: failed to load session: {exc}")

        if tree is None:
            return error_result(
                call_id,
                "memorize: requires prior tool execution in this session (session not found)",
            )

        if not self._has_non_error_tool_use(tree):
            return error_result(
                call_id,
                "memorize: requires at least one successful tool execution before memorizing",
            )

        session_key = _derive_session_key(context.session_id)
        now = time.time()
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            layer=layer,
            type=entry_type,
            content=content,
            source_session_id=context.session_id,
            created_at=now,
            updated_at=now,
        )

        try:
            await self._memory_store.store(session_key, entry)
        except Exception as exc:
            return error_result(call_id, f"memorize: store failed: {exc}")

        return text_result(
            call_id,
            f"memorized ({layer}/{entry_type}): {content[:80]}"
            + ("..." if len(content) > 80 else ""),
        )

    @staticmethod
    def _has_non_error_tool_use(tree) -> bool:
        from pyclaw.models import MessageEntry

        for entry in tree.entries.values():
            if isinstance(entry, MessageEntry) and entry.role == "tool":
                return True
        return False


def _derive_session_key(session_id: str) -> str:
    idx = session_id.find(":s:")
    return session_id[:idx] if idx != -1 else session_id
