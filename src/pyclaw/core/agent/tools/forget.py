"""ForgetTool — agent 主动归档失败/过时的 SOP。"""
from __future__ import annotations

import asyncio
from typing import Any

from pyclaw.core.agent.tools.registry import ToolContext, error_result, text_result
from pyclaw.models import MessageEntry, ToolResult
from pyclaw.storage.memory.base import MemoryStore
from pyclaw.storage.protocols import SessionStore


class ForgetTool:
    name = "forget"
    description = (
        "将一条已学习的 SOP/记忆标记为归档，使其不再在未来对话中被检索。"
        "当你发现某条已检索的记忆已过时、错误或不再适用时使用。"
        "需提供 entry_id（从 memory_context 中 [type|id] 格式可见的短 ID）和归档原因。"
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "entry_id": {
                "type": "string",
                "description": "要归档的记忆条目 ID（memory_context 中可见的 8 位短 ID 或完整 UUID）",
            },
            "reason": {
                "type": "string",
                "description": "归档原因（必填，如：步骤已过时、执行失败、环境已变更）",
            },
        },
        "required": ["entry_id", "reason"],
    }
    side_effect = True

    def __init__(self, memory_store: MemoryStore, session_store: SessionStore) -> None:
        self._memory_store = memory_store
        self._session_store = session_store

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        entry_id = args.get("entry_id", "").strip()
        reason = args.get("reason", "").strip()

        if not entry_id:
            return error_result(call_id, "forget: 'entry_id' 必填")
        if not reason:
            return error_result(call_id, "forget: 'reason' 必填，请说明归档原因")

        try:
            tree = await self._session_store.load(context.session_id)
        except Exception as exc:
            return error_result(call_id, f"forget: 加载 session 失败: {exc}")

        if tree is None:
            return error_result(
                call_id,
                "forget: 需要先在当前 session 执行过工具才能使用遗忘功能",
            )
        if not self._has_non_error_tool_use(tree):
            return error_result(
                call_id,
                "forget: 需要先在当前 session 成功执行过至少一次工具",
            )

        session_key = _derive_session_key(context.session_id)
        full_id, content_preview = await self._resolve_entry_id(session_key, entry_id)
        if full_id is None:
            return error_result(call_id, content_preview)

        archived = await self._memory_store.archive_entry(session_key, full_id, reason=reason)
        if not archived:
            return error_result(call_id, f"forget: 条目 '{entry_id}' 已处于归档状态或不存在")

        preview = content_preview[:50] + "..." if len(content_preview) > 50 else content_preview
        return text_result(call_id, f"已归档: {preview}\n原因: {reason}")

    async def _resolve_entry_id(
        self, session_key: str, entry_id: str
    ) -> tuple[str | None, str]:
        """Resolve short/full entry_id to (full_id, content) or (None, error_msg)."""
        sqlite = getattr(self._memory_store, "_sqlite", None)
        if sqlite is None:
            return None, "forget: 内部错误——无法访问存储后端"

        conn = await sqlite._get_conn(session_key)

        def _lookup() -> list[tuple[str, str]]:
            if len(entry_id) >= 32:
                rows = list(conn.execute(
                    "SELECT id, content FROM procedures WHERE id=? AND status='active'",
                    (entry_id,),
                ))
            else:
                rows = list(conn.execute(
                    "SELECT id, content FROM procedures WHERE id LIKE ? AND status='active'",
                    (f"{entry_id}%",),
                ))
            return [(r[0], r[1]) for r in rows]

        matches = await asyncio.to_thread(_lookup)

        if len(matches) == 0:
            return None, f"forget: 未找到 ID 匹配 '{entry_id}' 的活跃条目"
        if len(matches) > 1:
            return None, f"forget: '{entry_id}' 匹配多条记录({len(matches)}条)，请提供更长的 ID"

        return matches[0][0], matches[0][1]

    @staticmethod
    def _has_non_error_tool_use(tree) -> bool:
        """Check if session has at least one non-error tool call."""
        for entry in tree.entries.values():
            if isinstance(entry, MessageEntry) and entry.role == "tool":
                return True
        return False


def _derive_session_key(session_id: str) -> str:
    idx = session_id.find(":s:")
    return session_id[:idx] if idx != -1 else session_id
