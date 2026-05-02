from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from pyclaw.models import SessionTree
from pyclaw.models.session import now_iso
from pyclaw.storage.session.base import SessionStore


@dataclass
class SessionRouter:
    store: SessionStore
    on_session_rotated: Callable[[str], None] | None = field(default=None)

    async def resolve_or_create(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str = "default",
    ) -> tuple[str, SessionTree]:
        session_id = await self.store.get_current_session_id(session_key)
        if session_id is not None:
            tree = await self.store.load(session_id)
            if tree is not None:
                return session_id, tree

        old_tree = await self.store.load(session_key)
        if old_tree is not None:
            await self.store.set_current_session_id(session_key, session_key)
            return session_key, old_tree

        tree = await self.store.create_new_session(session_key, workspace_id, agent_id)
        return tree.header.id, tree

    async def rotate(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str = "default",
    ) -> tuple[str, SessionTree]:
        old_id = await self.store.get_current_session_id(session_key)
        tree = await self.store.create_new_session(
            session_key, workspace_id, agent_id, parent_session_id=old_id
        )
        if old_id is not None and self.on_session_rotated:
            self.on_session_rotated(old_id)
        return tree.header.id, tree

    async def update_last_interaction(self, session_id: str) -> None:
        tree = await self.store.load(session_id)
        if tree is None:
            return
        updated_header = tree.header.model_copy(update={"last_interaction_at": now_iso()})
        updated_tree = tree.model_copy(update={"header": updated_header})
        await self.store.save_header(updated_tree)

    async def check_idle_reset(
        self,
        session_key: str,
        session_id: str,
        idle_minutes: int,
    ) -> bool:
        if idle_minutes <= 0:
            return False
        tree = await self.store.load(session_id)
        if tree is None:
            return False
        last_at = tree.header.last_interaction_at
        if last_at is None:
            return False
        try:
            last_dt = datetime.fromisoformat(last_at)
        except ValueError:
            return False
        now_dt = datetime.now(timezone.utc)
        elapsed_seconds = (now_dt - last_dt).total_seconds()
        return elapsed_seconds >= idle_minutes * 60
