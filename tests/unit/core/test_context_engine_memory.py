from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.storage.memory.base import MemoryEntry


def _entry(entry_id: str, content: str, layer: str = "L2", type_: str = "fact") -> MemoryEntry:
    import time

    return MemoryEntry(
        id=entry_id,
        layer=layer,  # type: ignore[arg-type]
        type=type_,
        content=content,
        created_at=time.time(),
        updated_at=time.time(),
    )


@pytest.mark.asyncio
async def test_get_l1_snapshot_cached_per_session() -> None:
    ms = AsyncMock()
    ms.search_archives.return_value = []
    ms.index_get.return_value = [_entry("m1", "fact one"), _entry("m2", "fact two")]
    engine = DefaultContextEngine(memory_store=ms)

    session_id = "feishu:cli_x:ou_abc:s:alpha"
    first = await engine.get_l1_snapshot(session_id)
    second = await engine.get_l1_snapshot(session_id)

    assert first is second
    assert ms.index_get.await_count == 1


@pytest.mark.asyncio
async def test_get_l1_snapshot_different_sessions_independent() -> None:
    ms = AsyncMock()
    ms.search_archives.return_value = []
    ms.index_get.side_effect = [
        [_entry("a1", "A content")],
        [_entry("b1", "B content")],
    ]
    engine = DefaultContextEngine(memory_store=ms)

    result_a = await engine.get_l1_snapshot("feishu:a:s:x1")
    result_b = await engine.get_l1_snapshot("feishu:b:s:x2")

    assert result_a[0].content == "A content"
    assert result_b[0].content == "B content"
    assert ms.index_get.await_count == 2


@pytest.mark.asyncio
async def test_get_l1_snapshot_no_memory_store_returns_empty() -> None:
    engine = DefaultContextEngine()
    assert await engine.get_l1_snapshot("any-session") == []


@pytest.mark.asyncio
async def test_get_l1_snapshot_handles_store_error_gracefully() -> None:
    ms = AsyncMock()
    ms.search_archives.return_value = []
    ms.index_get.side_effect = RuntimeError("redis down")
    engine = DefaultContextEngine(memory_store=ms)

    result = await engine.get_l1_snapshot("feishu:a:s:x")
    assert result == []


@pytest.mark.asyncio
async def test_get_l1_snapshot_derives_session_key_correctly() -> None:
    ms = AsyncMock()
    ms.search_archives.return_value = []
    ms.index_get.return_value = []
    engine = DefaultContextEngine(memory_store=ms)

    await engine.get_l1_snapshot("feishu:cli_x:ou_abc:s:session12")

    ms.index_get.assert_awaited_once_with("feishu:cli_x:ou_abc")


@pytest.mark.asyncio
async def test_l1_snapshot_appears_in_frozen_prefix() -> None:
    from pyclaw.core.agent.runner import _format_l1_snapshot
    from pyclaw.core.agent.system_prompt import PromptInputs, build_frozen_prefix

    entries = [_entry("m1", "user prefers concise"), _entry("m2", "uses Redis")]
    text = _format_l1_snapshot(entries)

    inputs = PromptInputs(
        session_id="s:a:s:x",
        workspace_id="a",
        agent_id="default",
        model="gpt-4o",
        tools=(),
    )
    result = build_frozen_prefix(inputs, budget=2048, l1_snapshot=text)

    assert "l1_snapshot" in result.token_breakdown
    assert "<memory_index>" in result.text
    assert "user prefers concise" in result.text
    assert "uses Redis" in result.text


@pytest.mark.asyncio
async def test_enforce_system_budget_can_truncate_l1_snapshot() -> None:
    from pyclaw.core.agent.system_prompt import PromptInputs, build_frozen_prefix

    big_l1 = "<memory_index>\n" + ("- very long memory content\n" * 400) + "</memory_index>"

    inputs = PromptInputs(
        session_id="s:a:s:x",
        workspace_id="a",
        agent_id="default",
        model="gpt-4o",
        tools=(),
    )
    result = build_frozen_prefix(inputs, budget=200, l1_snapshot=big_l1)

    l1_tokens = result.token_breakdown.get("l1_snapshot", 0)
    assert l1_tokens < len(big_l1) // 4


@pytest.mark.asyncio
async def test_assemble_searches_memory_store_when_prompt_provided() -> None:
    ms = AsyncMock()
    ms.search_archives.return_value = []
    ms.search.return_value = [
        _entry("r1", "prefers short answers", layer="L2", type_="user_preference"),
        _entry("r2", "deploys via GitHub Actions", layer="L3", type_="workflow"),
    ]
    engine = DefaultContextEngine(memory_store=ms)

    result = await engine.assemble(
        session_id="feishu:a:s:x",
        messages=[{"role": "user", "content": "how to deploy?"}],
        prompt="how to deploy?",
    )

    ms.search.assert_awaited_once_with(
        "feishu:a", "how to deploy?", layers=["L2", "L3"], per_layer_limits={"L2": 3, "L3": 2}
    )
    assert result.system_prompt_addition is not None
    assert "<memory_context>" in result.system_prompt_addition
    assert "<facts>" in result.system_prompt_addition
    assert "<procedures>" in result.system_prompt_addition
    assert "prefers short answers" in result.system_prompt_addition
    assert "[user_preference]" in result.system_prompt_addition
    assert "[workflow|" in result.system_prompt_addition


@pytest.mark.asyncio
async def test_assemble_without_memory_store_returns_none() -> None:
    engine = DefaultContextEngine()
    result = await engine.assemble(
        session_id="s:a:s:x",
        messages=[],
        prompt="anything",
    )
    assert result.system_prompt_addition is None


@pytest.mark.asyncio
async def test_assemble_skips_search_when_no_prompt() -> None:
    ms = AsyncMock()
    ms.search_archives.return_value = []
    engine = DefaultContextEngine(memory_store=ms)

    result = await engine.assemble(
        session_id="s:a:s:x",
        messages=[],
        prompt=None,
    )

    ms.search.assert_not_awaited()
    assert result.system_prompt_addition is None


@pytest.mark.asyncio
async def test_assemble_empty_search_results_returns_none_addition() -> None:
    ms = AsyncMock()
    ms.search_archives.return_value = []
    ms.search.return_value = []
    engine = DefaultContextEngine(memory_store=ms)

    result = await engine.assemble(
        session_id="s:a:s:x",
        messages=[],
        prompt="no match",
    )
    assert result.system_prompt_addition is None


@pytest.mark.asyncio
async def test_assemble_search_error_does_not_raise() -> None:
    ms = AsyncMock()
    ms.search_archives.return_value = []
    ms.search.side_effect = RuntimeError("boom")
    engine = DefaultContextEngine(memory_store=ms)

    result = await engine.assemble(
        session_id="s:a:s:x",
        messages=[],
        prompt="trigger error",
    )
    assert result.system_prompt_addition is None
