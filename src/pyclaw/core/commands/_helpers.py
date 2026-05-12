from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from pyclaw.core.agent.runner import AgentRunnerDeps
    from pyclaw.core.commands.spec import CommandSpec
    from pyclaw.core.sop_extraction import ExtractionResult

logger = logging.getLogger(__name__)

EXTRACT_TIMEOUT_SECONDS = 15.0


class _IdleQueue(Protocol):
    def is_idle(self, key: str) -> bool: ...


async def idle_guard_check(
    spec: "CommandSpec",
    queue_obj: _IdleQueue,
    key: str,
    reply: Callable[[str], Awaitable[None]],
) -> bool:
    if not spec.requires_idle:
        return False
    if queue_obj.is_idle(key):
        return False
    await reply("⏳ 任务运行中，请先 /stop 或等待结束")
    return True


async def check_idle(
    queue_obj: _IdleQueue,
    key: str,
    reply: Callable[[str], Awaitable[None]],
) -> bool:
    """Sub-command idle guard (spec-free).

    Returns True when the handler should short-circuit (queue is busy and user
    was informed). Returns False when the queue is idle and the handler may
    proceed. Unlike :func:`idle_guard_check`, this helper does not read
    ``CommandSpec.requires_idle`` — callers from within commands that declare
    ``requires_idle=False`` use this for finer sub-command-level gating.
    """
    if queue_obj.is_idle(key):
        return False
    await reply("⏳ 任务运行中，请先 /stop 或等待结束")
    return True


def list_available_models(agent_settings: Any) -> dict[str, list[str]]:
    providers = getattr(agent_settings, "providers", None) or {}
    result: dict[str, list[str]] = {}
    for name, ps in providers.items():
        models_dict = getattr(ps, "models", None) or {}
        ids = list(models_dict.keys())
        if ids:
            result[name] = ids
    return result


def list_available_models_with_modalities(agent_settings: Any) -> dict[str, list[tuple[str, Any]]]:
    providers = getattr(agent_settings, "providers", None) or {}
    result: dict[str, list[tuple[str, Any]]] = {}
    for name, ps in providers.items():
        models_dict = getattr(ps, "models", None) or {}
        if not models_dict:
            continue
        result[name] = [(mid, entry.modalities) for mid, entry in models_dict.items()]
    return result


def parse_idle_duration(arg: str) -> int | None:
    arg = arg.strip().lower()
    if arg in ("off", "0", "disable", "关闭"):
        return 0
    m = re.fullmatch(r"(\d+)m(?:ins?|inutes?)?", arg)
    if m:
        return int(m.group(1))
    m = re.fullmatch(r"(\d+)h(?:ours?)?", arg)
    if m:
        return int(m.group(1)) * 60
    return None


async def format_session_status(
    session_key: str,
    session_id: str,
    deps: "AgentRunnerDeps",
) -> str:
    tree = await deps.session_store.load(session_id)
    msg_count = len(tree.entries) if tree else 0
    created_at = tree.header.created_at if tree else "unknown"
    short_id = session_id.split(":")[-1] if ":" in session_id else session_id[-8:]
    model = (tree.header.model_override if tree else None) or (
        deps.llm.default_model if hasattr(deps, "llm") else "unknown"
    )
    lines = [
        "📊 **会话状态**",
        f"SessionKey: `{session_key}`",
        f"SessionId:  `...{short_id}`",
        f"消息数:     {msg_count}",
        f"模型:       {model}",
        f"创建时间:   {created_at[:19].replace('T', ' ')}",
    ]
    return "\n".join(lines)


async def run_extract(
    *,
    redis_client: Any,
    memory_store: Any,
    session_store: Any,
    llm_client: Any,
    session_id: str,
    settings: Any,
    nudge_hook: Any = None,
    timeout: float = EXTRACT_TIMEOUT_SECONDS,
) -> "ExtractionResult | None":
    """Run SOP extraction with timeout. Returns ExtractionResult, or None if timed out, or a synthetic disabled result if any dep is missing."""
    from pyclaw.core.sop_extraction import (
        ExtractionResult,
        _check_user_ratelimit,
        _derive_session_key,
        extract_sops_sync,
    )

    if (
        redis_client is None
        or memory_store is None
        or settings is None
        or llm_client is None
        or session_store is None
    ):
        return ExtractionResult(spawned=False, skip_reason="disabled")

    session_key = _derive_session_key(session_id)
    if not await _check_user_ratelimit(redis_client, session_key):
        return ExtractionResult(spawned=False, skip_reason="rate_limited")

    try:
        return await asyncio.wait_for(
            extract_sops_sync(
                memory_store=memory_store,
                session_store=session_store,
                redis_client=redis_client,
                llm_client=llm_client,
                session_id=session_id,
                settings=settings,
                min_tool_calls=1,
                nudge_hook=nudge_hook,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        return None
