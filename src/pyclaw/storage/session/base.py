from __future__ import annotations

from typing import Protocol, runtime_checkable

from pyclaw.models import SessionEntry, SessionTree


@runtime_checkable
class SessionStore(Protocol):
    async def load(self, session_id: str) -> SessionTree | None: ...
    async def save_header(self, tree: SessionTree) -> None: ...
    async def append_entry(self, session_id: str, entry: SessionEntry, leaf_id: str) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._trees: dict[str, SessionTree] = {}

    async def load(self, session_id: str) -> SessionTree | None:
        tree = self._trees.get(session_id)
        if tree is None:
            return None
        return tree.model_copy(deep=True)

    async def save_header(self, tree: SessionTree) -> None:
        self._trees[tree.header.id] = tree.model_copy(deep=True)

    async def append_entry(self, session_id: str, entry: SessionEntry, leaf_id: str) -> None:
        tree = self._trees.get(session_id)
        if tree is None:
            raise KeyError(f"session {session_id} not initialized; call save_header first")
        tree.entries[entry.id] = entry.model_copy(deep=True)
        tree.order.append(entry.id)
        tree.leaf_id = leaf_id
