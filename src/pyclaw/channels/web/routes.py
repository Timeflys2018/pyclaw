from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from pyclaw.channels.session_router import SessionRouter
from pyclaw.channels.web.auth import get_current_user
from pyclaw.models.session import MessageEntry, SessionTree
from pyclaw.storage.session.base import SessionStore

web_router = APIRouter(prefix="/api", tags=["web"])

_store: SessionStore | None = None
_session_router: SessionRouter | None = None


def set_web_deps(store: SessionStore, session_router: SessionRouter) -> None:
    global _store, _session_router
    _store = store
    _session_router = session_router


def _get_store() -> SessionStore:
    if _store is None:
        raise RuntimeError("Web deps not initialised")
    return _store


def _get_router() -> SessionRouter:
    if _session_router is None:
        raise RuntimeError("Web deps not initialised")
    return _session_router


class SessionListItem(BaseModel):
    id: str
    created_at: str
    message_count: int
    last_interaction_at: str | None
    parent_session_id: str | None
    title: str | None = None


class CreateSessionResponse(BaseModel):
    session_id: str


async def _load_and_verify(
    session_id: str, user_id: str
) -> SessionTree:
    store = _get_store()
    tree = await store.load(session_id)
    if tree is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if not tree.header.session_key.startswith(f"web:{user_id}"):
        raise HTTPException(status_code=403, detail="Access denied")
    return tree


@web_router.get("/sessions")
async def list_sessions(
    user_id: str = Depends(get_current_user),
) -> list[SessionListItem]:
    store = _get_store()
    session_key = f"web:{user_id}"
    summaries = await store.list_session_history(session_key)
    items: list[SessionListItem] = []
    for s in summaries:
        title: str | None = None
        tree = await store.load(s.session_id)
        if tree is not None:
            ordered = [tree.entries[eid] for eid in tree.order if eid in tree.entries]
            for entry in ordered:
                if isinstance(entry, MessageEntry) and entry.role == "user":
                    content = entry.content if isinstance(entry.content, str) else ""
                    title = content[:50] if content else None
                    break
        items.append(
            SessionListItem(
                id=s.session_id,
                created_at=s.created_at,
                message_count=s.message_count,
                last_interaction_at=s.last_message_at,
                parent_session_id=s.parent_session_id,
                title=title,
            )
        )
    return items


@web_router.post("/sessions")
async def create_session(
    user_id: str = Depends(get_current_user),
) -> CreateSessionResponse:
    router = _get_router()
    session_key = f"web:{user_id}"
    session_id, _tree = await router.rotate(session_key, "default")
    return CreateSessionResponse(session_id=session_id)


@web_router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
) -> dict:
    tree = await _load_and_verify(session_id, user_id)
    h = tree.header
    msg_count = sum(1 for e in tree.entries.values() if isinstance(e, MessageEntry))
    return {
        "id": h.id,
        "workspace_id": h.workspace_id,
        "agent_id": h.agent_id,
        "created_at": h.created_at,
        "last_interaction_at": h.last_interaction_at,
        "message_count": msg_count,
    }


@web_router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    offset: int = 0,
    limit: int = 50,
    user_id: str = Depends(get_current_user),
) -> list[dict]:
    tree = await _load_and_verify(session_id, user_id)
    ordered = [tree.entries[eid] for eid in tree.order if eid in tree.entries]
    msg_entries = [e for e in ordered if isinstance(e, MessageEntry)]
    page = msg_entries[offset : offset + limit]
    return [
        {
            "id": e.id,
            "role": e.role,
            "content": e.content,
            "timestamp": e.timestamp,
        }
        for e in page
    ]


@web_router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str,
    user_id: str = Depends(get_current_user),
) -> Response:
    await _load_and_verify(session_id, user_id)
    return Response(status_code=204)
