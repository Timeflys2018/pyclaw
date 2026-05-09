"""Tests for format_session_status reading tree.header.model_override.

Covers spec status-truthfulness scenarios (6 scenarios → 4 test functions via parametrize).
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock

import pytest

from pyclaw.core.commands._helpers import format_session_status
from pyclaw.models import SessionHeader, SessionTree


def _build_tree(model_override: str | None) -> SessionTree:
    header = SessionHeader(
        id="web:user1:s:abc123",
        workspace_id="default",
        agent_id="default",
        model_override=model_override,
    )
    return SessionTree(header=header)


def _build_deps(
    *,
    tree: SessionTree | None,
    default_model: str | None = "anthropic/ppio/pa/claude-sonnet-4-6",
) -> object:
    session_store = types.SimpleNamespace(load=AsyncMock(return_value=tree))
    if default_model is None:
        return types.SimpleNamespace(session_store=session_store)
    llm = types.SimpleNamespace(default_model=default_model)
    return types.SimpleNamespace(session_store=session_store, llm=llm)


@pytest.mark.asyncio
async def test_model_override_set_displays_override() -> None:
    """Scenario: model_override is set, /status displays the override."""
    tree = _build_tree(model_override="anthropic/ppio/pa/claude-opus-4-6")
    deps = _build_deps(tree=tree, default_model="anthropic/ppio/pa/claude-sonnet-4-6")

    output = await format_session_status("user1", "web:user1:s:abc123", deps)

    assert "模型:       anthropic/ppio/pa/claude-opus-4-6" in output
    assert "claude-sonnet-4-6" not in output


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [None, ""])
async def test_model_override_falsy_falls_back_to_default(override: str | None) -> None:
    """Scenarios: model_override is None / empty string → fall back to default_model."""
    tree = _build_tree(model_override=override)
    deps = _build_deps(tree=tree, default_model="anthropic/ppio/pa/claude-sonnet-4-6")

    output = await format_session_status("user1", "web:user1:s:abc123", deps)

    assert "模型:       anthropic/ppio/pa/claude-sonnet-4-6" in output


@pytest.mark.asyncio
async def test_tree_none_falls_back_to_default() -> None:
    """Scenario: tree is None (session not yet persisted) → no AttributeError, fall back to default_model."""
    deps = _build_deps(tree=None, default_model="anthropic/ppio/pa/claude-sonnet-4-6")

    output = await format_session_status("user1", "web:user1:s:abc123", deps)

    assert "模型:       anthropic/ppio/pa/claude-sonnet-4-6" in output
    assert "创建时间:   unknown" in output
    assert "消息数:     0" in output


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_override,expected_model",
    [
        (None, "unknown"),
        ("anthropic/ppio/pa/claude-opus-4-6", "anthropic/ppio/pa/claude-opus-4-6"),
    ],
)
async def test_deps_llm_missing_with_or_without_override(
    model_override: str | None,
    expected_model: str,
) -> None:
    """Scenarios: deps.llm missing — without override displays 'unknown';
    with override still displays the override (priority chain preserved)."""
    tree = _build_tree(model_override=model_override)
    deps = _build_deps(tree=tree, default_model=None)

    assert not hasattr(deps, "llm")

    output = await format_session_status("user1", "web:user1:s:abc123", deps)

    assert f"模型:       {expected_model}" in output
