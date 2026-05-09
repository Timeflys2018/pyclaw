from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.commands.builtin import cmd_model
from pyclaw.core.commands.context import CommandContext
from pyclaw.infra.settings import AgentSettings, ProviderSettings
from pyclaw.models import (
    ModelChangeEntry,
    SessionHeader,
    SessionTree,
)
from pyclaw.storage.session.base import InMemorySessionStore


def _make_settings_with_models() -> AgentSettings:
    return AgentSettings(
        providers={
            "openai": ProviderSettings(api_key="sk-x", models=["gpt-4o", "gpt-4o-mini"]),
            "anthropic": ProviderSettings(
                api_key="sk-y",
                base_url="https://api.anthropic.com",
                models=["claude-sonnet-4-20250514"],
            ),
        }
    )


async def _make_ctx(
    *,
    session_id: str = "sid-1",
    agent_settings: Any = None,
    default_model: str = "gpt-4o",
) -> tuple[CommandContext, AsyncMock, InMemorySessionStore]:
    store = InMemorySessionStore()
    header = SessionHeader(id=session_id, workspace_id="ws", agent_id="default", session_key="key")
    tree = SessionTree(header=header)
    await store.save_header(tree)

    deps = MagicMock()
    deps.session_store = store
    deps.llm = MagicMock()
    deps.llm.default_model = default_model

    reply = AsyncMock()
    ctx = CommandContext(
        session_id=session_id,
        session_key="key",
        workspace_id="ws",
        user_id="u",
        channel="web",
        deps=deps,
        session_router=SessionRouter(store=store),
        workspace_base=Path("/tmp"),
        reply=reply,
        dispatch_user_message=AsyncMock(),
        raw={"channel": "web"},
        agent_settings=agent_settings,
    )
    return ctx, reply, store


@pytest.mark.asyncio
async def test_model_no_args_shows_default_when_no_override() -> None:
    ctx, reply, _ = await _make_ctx(agent_settings=_make_settings_with_models())

    await cmd_model("", ctx)

    msg = reply.await_args[0][0]
    assert "gpt-4o" in msg
    assert "📦 openai" in msg
    assert "📦 anthropic" in msg
    assert "claude-sonnet-4-20250514" in msg


@pytest.mark.asyncio
async def test_model_no_args_shows_override_when_set() -> None:
    ctx, reply, store = await _make_ctx(agent_settings=_make_settings_with_models())
    tree = await store.load(ctx.session_id)
    assert tree is not None
    tree.header = tree.header.model_copy(update={"model_override": "claude-sonnet-4-20250514"})
    await store.save_header(tree)

    await cmd_model("", ctx)

    msg = reply.await_args[0][0]
    assert "claude-sonnet-4-20250514" in msg


@pytest.mark.asyncio
async def test_model_no_args_warns_if_no_models_configured() -> None:
    empty_settings = AgentSettings(providers={"openai": ProviderSettings(api_key="sk-x")})
    ctx, reply, _ = await _make_ctx(agent_settings=empty_settings)

    await cmd_model("", ctx)

    msg = reply.await_args[0][0]
    assert "尚未声明可用模型列表" in msg


@pytest.mark.asyncio
async def test_model_with_arg_writes_override_and_appends_entry() -> None:
    ctx, reply, store = await _make_ctx(agent_settings=_make_settings_with_models())

    await cmd_model("claude-sonnet-4-20250514", ctx)

    msg = reply.await_args[0][0]
    assert "✓" in msg
    assert "claude-sonnet-4-20250514" in msg

    tree = await store.load(ctx.session_id)
    assert tree is not None
    assert tree.header.model_override == "claude-sonnet-4-20250514"

    entries = list(tree.entries.values())
    model_entries = [e for e in entries if isinstance(e, ModelChangeEntry)]
    assert len(model_entries) == 1
    assert model_entries[0].model_id == "claude-sonnet-4-20250514"
    assert model_entries[0].provider == "anthropic"


@pytest.mark.asyncio
async def test_model_with_unknown_arg_still_persists_override() -> None:
    ctx, reply, store = await _make_ctx(agent_settings=_make_settings_with_models())

    await cmd_model("some-unknown-model-xyz", ctx)

    tree = await store.load(ctx.session_id)
    assert tree is not None
    assert tree.header.model_override == "some-unknown-model-xyz"

    model_entries = [e for e in tree.entries.values() if isinstance(e, ModelChangeEntry)]
    assert len(model_entries) == 1
    assert model_entries[0].provider == "unknown"


@pytest.mark.asyncio
async def test_model_runner_three_level_fallback_priority() -> None:
    from pyclaw.core.agent.runner import RunRequest

    settings = _make_settings_with_models()
    _, _, store = await _make_ctx(agent_settings=settings)
    sid = "sid-1"
    tree = await store.load(sid)
    assert tree is not None

    request_with_explicit = RunRequest(
        session_id=sid, workspace_id="ws", agent_id="a", user_message="hi", model="explicit-model",
    )
    expected_explicit = (
        request_with_explicit.model
        or tree.header.model_override
        or "default-from-llm-client"
    )
    assert expected_explicit == "explicit-model"

    tree2 = tree.model_copy(update={"header": tree.header.model_copy(update={"model_override": "override-model"})})
    request_no_explicit = RunRequest(
        session_id=sid, workspace_id="ws", agent_id="a", user_message="hi",
    )
    expected_override = (
        request_no_explicit.model
        or tree2.header.model_override
        or "default-from-llm-client"
    )
    assert expected_override == "override-model"

    tree3 = tree.model_copy(update={"header": tree.header.model_copy(update={"model_override": None})})
    expected_default = (
        request_no_explicit.model
        or tree3.header.model_override
        or "default-from-llm-client"
    )
    assert expected_default == "default-from-llm-client"
