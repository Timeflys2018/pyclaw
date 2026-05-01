from __future__ import annotations

import secrets
import time
from typing import Protocol, runtime_checkable

from pyclaw.models import SessionEntry, SessionTree
from pyclaw.models.session import SessionHeader, SessionHistorySummary, now_iso


@runtime_checkable
class SessionStore(Protocol):
    async def load(self, session_id: str) -> SessionTree | None: ...
    async def save_header(self, tree: SessionTree) -> None: ...
    async def append_entry(self, session_id: str, entry: SessionEntry, leaf_id: str) -> None: ...

    async def get_current_session_id(self, session_key: str) -> str | None: ...
    async def set_current_session_id(self, session_key: str, session_id: str) -> None: ...
    async def create_new_session(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str,
        parent_session_id: str | None = None,
    ) -> SessionTree: ...
    async def list_session_history(
        self, session_key: str, limit: int = 20
    ) -> list[SessionHistorySummary]: ...


def _generate_session_id(session_key: str) -> str:
    suffix = secrets.token_hex(4)
    return f"{session_key}:s:{suffix}"


class InMemorySessionStore:
    def __init__(self) -> None:
        self._trees: dict[str, SessionTree] = {}
        self._skey_current: dict[str, str] = {}
        self._skey_history: dict[str, list[tuple[float, str]]] = {}

    async def load(self, session_id: str) -> SessionTree | None:
        tree = self._trees.get(session_id)
        if tree is None:
            return None
        return tree.model_copy(deep=True)

    async def save_header(self, tree: SessionTree) -> None:
        sid = tree.header.id
        if sid in self._trees:
            self._trees[sid] = self._trees[sid].model_copy(
                update={"header": tree.header.model_copy()}, deep=True
            )
        else:
            self._trees[sid] = tree.model_copy(deep=True)

    async def append_entry(self, session_id: str, entry: SessionEntry, leaf_id: str) -> None:
        tree = self._trees.get(session_id)
        if tree is None:
            raise KeyError(f"session {session_id} not initialized; call save_header first")
        tree.entries[entry.id] = entry.model_copy(deep=True)
        tree.order.append(entry.id)
        tree.leaf_id = leaf_id

    async def get_current_session_id(self, session_key: str) -> str | None:
        return self._skey_current.get(session_key)

    async def set_current_session_id(self, session_key: str, session_id: str) -> None:
        self._skey_current[session_key] = session_id
        if session_key not in self._skey_history:
            self._skey_history[session_key] = []
        ts = time.time() * 1000
        self._skey_history[session_key].append((ts, session_id))

    async def create_new_session(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str,
        parent_session_id: str | None = None,
    ) -> SessionTree:
        session_id = _generate_session_id(session_key)
        header = SessionHeader(
            id=session_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            session_key=session_key,
            parent_session=parent_session_id,
        )
        tree = SessionTree(header=header)
        self._trees[session_id] = tree.model_copy(deep=True)
        await self.set_current_session_id(session_key, session_id)
        return tree

    async def list_session_history(
        self, session_key: str, limit: int = 20
    ) -> list[SessionHistorySummary]:
        entries = self._skey_history.get(session_key, [])
        sorted_entries = sorted(entries, key=lambda x: x[0], reverse=True)[:limit]
        result: list[SessionHistorySummary] = []
        for _ts, sid in sorted_entries:
            tree = self._trees.get(sid)
            if tree is None:
                result.append(SessionHistorySummary(
                    session_id=sid,
                    created_at="",
                    message_count=0,
                    last_message_at=None,
                    parent_session_id=None,
                ))
                continue
            msg_entries = [e for e in tree.entries.values() if hasattr(e, "role")]
            last_ts = msg_entries[-1].timestamp if msg_entries else None
            result.append(SessionHistorySummary(
                session_id=sid,
                created_at=tree.header.created_at,
                message_count=len(msg_entries),
                last_message_at=last_ts,
                parent_session_id=tree.header.parent_session,
            ))
        return result
