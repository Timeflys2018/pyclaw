from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.commands.builtin import cmd_model
from pyclaw.core.commands.context import CommandContext
from pyclaw.infra.settings import (
    AgentSettings,
    ModelEntry,
    ModelModalities,
    ProviderSettings,
    Settings,
)
from pyclaw.models import (
    ModelChangeEntry,
    SessionHeader,
    SessionTree,
)
from pyclaw.storage.session.base import InMemorySessionStore


def _text_only_entry() -> ModelEntry:
    return ModelEntry(modalities=ModelModalities(input={"text"}, output={"text"}))


def _make_settings_with_models() -> AgentSettings:
    return AgentSettings(
        providers={
            "openai": ProviderSettings(
                api_key="sk-x",
                models={
                    "gpt-4o": _text_only_entry(),
                    "gpt-4o-mini": _text_only_entry(),
                },
            ),
            "anthropic": ProviderSettings(
                api_key="sk-y",
                base_url="https://api.anthropic.com",
                models={"claude-sonnet-4-20250514": _text_only_entry()},
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
        settings=Settings(),
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
async def test_model_with_unknown_arg_rejected_by_dry_run() -> None:
    ctx, reply, store = await _make_ctx(agent_settings=_make_settings_with_models())

    await cmd_model("vertex_ai/gemini-2.5-pro", ctx)

    msg = reply.await_args[0][0]
    assert "❌" in msg
    assert "vertex_ai/gemini-2.5-pro" in msg

    tree = await store.load(ctx.session_id)
    assert tree is not None
    assert tree.header.model_override is None
    model_entries = [e for e in tree.entries.values() if isinstance(e, ModelChangeEntry)]
    assert len(model_entries) == 0


@pytest.mark.asyncio
async def test_model_dry_run_uses_strict_mode_ignoring_settings_default_policy() -> None:
    settings = AgentSettings(
        providers={
            "openai": ProviderSettings(api_key="sk-x", prefixes=["openai"]),
            "anthropic": ProviderSettings(api_key="sk-y", prefixes=["anthropic"]),
        },
        default_provider="openai",
        unknown_prefix_policy="default",
    )
    ctx, reply, store = await _make_ctx(agent_settings=settings)

    await cmd_model("totally-fake-prefix-xyz", ctx)

    msg = reply.await_args[0][0]
    assert "❌" in msg
    tree = await store.load(ctx.session_id)
    assert tree is not None
    assert tree.header.model_override is None


@pytest.mark.asyncio
async def test_model_dry_run_state_not_polluted_after_rejection() -> None:
    ctx, reply, store = await _make_ctx(agent_settings=_make_settings_with_models())

    await cmd_model("claude-sonnet-4-20250514", ctx)
    tree = await store.load(ctx.session_id)
    assert tree.header.model_override == "claude-sonnet-4-20250514"

    await cmd_model("vertex_ai/foo", ctx)
    tree = await store.load(ctx.session_id)
    assert tree.header.model_override == "claude-sonnet-4-20250514"


@pytest.mark.asyncio
async def test_model_runner_three_level_fallback_priority() -> None:
    from pyclaw.core.agent.runner import RunRequest

    settings = _make_settings_with_models()
    _, _, store = await _make_ctx(agent_settings=settings)
    sid = "sid-1"
    tree = await store.load(sid)
    assert tree is not None

    request_with_explicit = RunRequest(
        session_id=sid,
        workspace_id="ws",
        agent_id="a",
        user_message="hi",
        model="explicit-model",
    )
    expected_explicit = (
        request_with_explicit.model or tree.header.model_override or "default-from-llm-client"
    )
    assert expected_explicit == "explicit-model"

    tree2 = tree.model_copy(
        update={"header": tree.header.model_copy(update={"model_override": "override-model"})}
    )
    request_no_explicit = RunRequest(
        session_id=sid,
        workspace_id="ws",
        agent_id="a",
        user_message="hi",
    )
    expected_override = (
        request_no_explicit.model or tree2.header.model_override or "default-from-llm-client"
    )
    assert expected_override == "override-model"

    tree3 = tree.model_copy(
        update={"header": tree.header.model_copy(update={"model_override": None})}
    )
    expected_default = (
        request_no_explicit.model or tree3.header.model_override or "default-from-llm-client"
    )
    assert expected_default == "default-from-llm-client"


def _make_vision_text_settings() -> AgentSettings:
    return AgentSettings(
        providers={
            "openai": ProviderSettings(
                api_key="sk-x",
                prefixes=["azure_openai"],
                models={
                    "azure_openai/gpt-5.4": ModelEntry(
                        modalities=ModelModalities(input={"text", "image"}, output={"text"})
                    ),
                    "azure_openai/gpt-5.3-codex": ModelEntry(
                        modalities=ModelModalities(input={"text"}, output={"text"})
                    ),
                },
            ),
        }
    )


class TestCmdModelModalityUX:
    @pytest.mark.asyncio
    async def test_model_no_args_shows_image_tag_for_vision_model(self) -> None:
        ctx, reply, _ = await _make_ctx(agent_settings=_make_vision_text_settings())
        await cmd_model("", ctx)
        msg = reply.await_args[0][0]
        assert "azure_openai/gpt-5.4 (image)" in msg

    @pytest.mark.asyncio
    async def test_model_no_args_omits_tag_for_text_only_model(self) -> None:
        ctx, reply, _ = await _make_ctx(agent_settings=_make_vision_text_settings())
        await cmd_model("", ctx)
        msg = reply.await_args[0][0]
        assert "azure_openai/gpt-5.3-codex" in msg
        codex_line = next(line for line in msg.splitlines() if "azure_openai/gpt-5.3-codex" in line)
        assert "(" not in codex_line

    @pytest.mark.asyncio
    async def test_model_no_args_sorted_modalities_in_tag(self) -> None:
        settings = AgentSettings(
            providers={
                "anthropic": ProviderSettings(
                    api_key="sk",
                    prefixes=["anthropic"],
                    models={
                        "anthropic/claude-opus-4-7": ModelEntry(
                            modalities=ModelModalities(
                                input={"text", "image", "pdf"}, output={"text"}
                            )
                        ),
                    },
                )
            }
        )
        ctx, reply, _ = await _make_ctx(agent_settings=settings)
        await cmd_model("", ctx)
        msg = reply.await_args[0][0]
        assert "anthropic/claude-opus-4-7 (image, pdf)" in msg

    @pytest.mark.asyncio
    async def test_switch_to_non_vision_model_appends_warning(self) -> None:
        ctx, reply, _ = await _make_ctx(agent_settings=_make_vision_text_settings())
        await cmd_model("azure_openai/gpt-5.3-codex", ctx)
        msg = reply.await_args[0][0]
        assert "✓" in msg
        assert "ℹ️" in msg
        assert "不支持图片处理" in msg

    @pytest.mark.asyncio
    async def test_switch_to_vision_model_no_warning(self) -> None:
        ctx, reply, _ = await _make_ctx(agent_settings=_make_vision_text_settings())
        await cmd_model("azure_openai/gpt-5.4", ctx)
        msg = reply.await_args[0][0]
        assert "✓" in msg
        assert "ℹ️" not in msg

    @pytest.mark.asyncio
    async def test_switch_to_undeclared_model_appends_warning_conservative(self) -> None:
        ctx, reply, _ = await _make_ctx(agent_settings=_make_vision_text_settings())
        await cmd_model("azure_openai/some-unknown-model", ctx)
        msg = reply.await_args[0][0]
        assert "✓" in msg
        assert "ℹ️" in msg
