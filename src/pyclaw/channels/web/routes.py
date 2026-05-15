from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from pyclaw.channels.session_router import SessionRouter
from pyclaw.channels.web.auth import get_current_user
from pyclaw.models.session import MessageEntry, SessionTree
from pyclaw.storage.session.base import SessionStore

logger = logging.getLogger(__name__)

web_router = APIRouter(prefix="/api", tags=["web"])

# TODO: Module-level globals are set once via set_web_deps() at lifespan startup.
# Production-safe (single-process per uvicorn worker), but causes flakiness in
# pytest-xdist parallel test runs. Future: refactor to FastAPI app.state DI.
# Tracked in: harden-self-evolution-extraction (deferred — architectural smell, no production bug).
_store: SessionStore | None = None
_session_router: SessionRouter | None = None
_memory_store: Any = None
_task_manager: Any = None
_redis_client: Any = None
_llm_client: Any = None
_evolution_settings: Any = None
_nudge_hook: Any = None


def set_web_deps(
    store: SessionStore,
    session_router: SessionRouter,
    *,
    memory_store: Any = None,
    task_manager: Any = None,
    redis_client: Any = None,
    llm_client: Any = None,
    evolution_settings: Any = None,
    nudge_hook: Any = None,
) -> None:
    global _store, _session_router, _memory_store, _task_manager
    global _redis_client, _llm_client, _evolution_settings, _nudge_hook
    _store = store
    _session_router = session_router
    _memory_store = memory_store
    _task_manager = task_manager
    _redis_client = redis_client
    _llm_client = llm_client
    _evolution_settings = evolution_settings
    _nudge_hook = nudge_hook


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


class ExtractResponse(BaseModel):
    spawned: bool
    message: str


class WebSettingsResponse(BaseModel):
    default_permission_tier: str
    tool_approval_timeout_seconds: int
    tools_requiring_approval: list[str]


@web_router.get("/settings", response_model=WebSettingsResponse)
async def get_web_settings(request: Request) -> WebSettingsResponse:
    web_deps = getattr(request.app.state, "web_deps", None)
    if web_deps is None:
        raise HTTPException(status_code=500, detail="Web deps not configured")
    web_settings = web_deps.settings_full.channels.web
    return WebSettingsResponse(
        default_permission_tier=web_settings.default_permission_tier,
        tool_approval_timeout_seconds=web_settings.tool_approval_timeout_seconds,
        tools_requiring_approval=list(web_settings.tools_requiring_approval),
    )


async def _load_and_verify(session_id: str, user_id: str) -> SessionTree:
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
            if tree.header.title:
                title = tree.header.title
            else:
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
    store = _get_store()
    deleted = await store.delete_session(session_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Session not found")
    return Response(status_code=204)


class PatchSessionRequest(BaseModel):
    title: str | None = None


@web_router.patch("/sessions/{session_id}")
async def patch_session(
    session_id: str,
    body: PatchSessionRequest,
    user_id: str = Depends(get_current_user),
) -> dict:
    tree = await _load_and_verify(session_id, user_id)
    store = _get_store()
    if body.title is not None:
        title = body.title.strip()
        if len(title) > 200:
            raise HTTPException(status_code=400, detail="Title too long (max 200 chars)")
        tree.header.title = title or None
        await store.save_header(tree)
    return {
        "id": tree.header.id,
        "title": tree.header.title,
    }


@web_router.post("/extract", response_model=ExtractResponse)
async def trigger_extract(
    user_id: str = Depends(get_current_user),
) -> ExtractResponse:
    return await _do_extract(user_id)


@web_router.post("/learn", response_model=ExtractResponse)
async def trigger_learn(
    user_id: str = Depends(get_current_user),
) -> ExtractResponse:
    return await _do_extract(user_id)


async def _do_extract(user_id: str) -> ExtractResponse:
    try:
        router = _get_router()
        session_key = f"web:{user_id}"
        session_id = await router.store.get_current_session_id(session_key)
        if session_id is None:
            return ExtractResponse(
                spawned=False,
                message="当前会话还没有 tool 调用。先让 bot 执行一些实际操作（bash/read/write 等），再试 /extract。",
            )

        from pyclaw.core.commands._helpers import run_extract
        from pyclaw.core.sop_extraction import format_extraction_result_zh

        result = await run_extract(
            redis_client=_redis_client,
            memory_store=_memory_store,
            session_store=router.store,
            llm_client=_llm_client,
            session_id=session_id,
            settings=_evolution_settings,
            nudge_hook=_nudge_hook,
        )

        if result is None:
            return ExtractResponse(
                spawned=False,
                message="⏳ 学习超时（>15 秒）已中止，候选数据已保留，1 分钟后可再次 /extract。",
            )
        if result.skip_reason == "disabled":
            return ExtractResponse(spawned=False, message="⚠️ 自我进化功能未启用。")
        if result.skip_reason == "rate_limited":
            return ExtractResponse(
                spawned=False,
                message="⏱ 学习触发过于频繁，请 1 分钟后再试。",
            )

        return ExtractResponse(
            spawned=result.spawned and result.skip_reason is None,
            message=format_extraction_result_zh(result),
        )
    except Exception:
        logger.exception("extract endpoint failed for user %s", user_id)
        return ExtractResponse(
            spawned=False,
            message="⚠️ 命令执行失败，请稍后重试。",
        )
