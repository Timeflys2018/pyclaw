from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from pyclaw.channels.session_router import SessionRouter
from pyclaw.channels.web.auth import get_current_user
from pyclaw.core.agent.runner import AgentRunnerDeps, RunRequest, run_agent_stream
from pyclaw.models.agent import Done, TextChunk

openai_router = APIRouter(prefix="/v1", tags=["openai"])

_deps: AgentRunnerDeps | None = None
_session_router: SessionRouter | None = None
_workspace_base: Path | None = None


def set_openai_deps(
    deps: AgentRunnerDeps,
    session_router: SessionRouter,
    workspace_base: Path | None = None,
) -> None:
    global _deps, _session_router, _workspace_base
    _deps = deps
    _session_router = session_router
    _workspace_base = workspace_base


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "default"
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    user: str | None = None


@openai_router.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest,
    user_id: str = Depends(get_current_user),
) -> Any:
    if _deps is None or _session_router is None:
        raise HTTPException(500, "OpenAI compat deps not configured")

    # Task 10.3: ignore body.user to prevent session spoofing
    session_key = f"openai:{user_id}"

    user_messages = [m for m in body.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(400, "No user message")
    last_msg = user_messages[-1].content

    session_id, _tree = await _session_router.resolve_or_create(
        session_key, "default"
    )

    request = RunRequest(
        session_id=session_id,
        workspace_id="default",
        agent_id="default",
        user_message=last_msg,
    )

    if body.stream:
        return StreamingResponse(
            _stream_sse(request, body.model, user_id),
            media_type="text/event-stream",
            headers={"X-Accel-Buffering": "no"},
        )

    return await _complete_response(request, body.model, user_id)


def _resolve_user_workspace(user_id: str) -> Path:
    assert _workspace_base is not None, "workspace_base not configured"
    user_workspace = _workspace_base / f"web_{user_id}"
    user_workspace.mkdir(parents=True, exist_ok=True)
    return user_workspace


async def _stream_sse(request: RunRequest, model: str, user_id: str):
    assert _deps is not None
    completion_id = f"chatcmpl-{secrets.token_hex(12)}"

    # Task 10.4: per-user workspace isolation
    tool_workspace = _resolve_user_workspace(user_id)

    first_chunk = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
        ],
    }
    yield f"data: {json.dumps(first_chunk)}\n\n"

    async for event in run_agent_stream(
        request, _deps, tool_workspace_path=tool_workspace
    ):
        if isinstance(event, TextChunk):
            chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": event.text},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        elif isinstance(event, Done):
            final = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": model,
                "choices": [
                    {"index": 0, "delta": {}, "finish_reason": "stop"}
                ],
            }
            yield f"data: {json.dumps(final)}\n\n"
            break

    yield "data: [DONE]\n\n"


async def _complete_response(request: RunRequest, model: str, user_id: str) -> dict[str, Any]:
    assert _deps is not None
    completion_id = f"chatcmpl-{secrets.token_hex(12)}"
    collected_text: list[str] = []
    usage: dict[str, int] = {}

    tool_workspace = _resolve_user_workspace(user_id)

    async for event in run_agent_stream(
        request, _deps, tool_workspace_path=tool_workspace
    ):
        if isinstance(event, TextChunk):
            collected_text.append(event.text)
        elif isinstance(event, Done):
            usage = event.usage
            break

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(collected_text),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


@openai_router.get("/models")
async def list_models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {"id": "pyclaw-default", "object": "model", "owned_by": "pyclaw"}
        ],
    }
