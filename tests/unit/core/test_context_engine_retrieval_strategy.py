from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest

from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.infra.settings import MemorySettings
from pyclaw.storage.memory.base import MemoryEntry


def _entry(eid: str, content: str, layer: str = "L2", type_: str = "fact") -> MemoryEntry:
    return MemoryEntry(
        id=eid,
        layer=layer,  # type: ignore[arg-type]
        type=type_,
        content=content,
        created_at=time.time(),
        updated_at=time.time(),
    )


async def test_assemble_uses_configured_quotas_not_hardcoded_five() -> None:
    ms = AsyncMock()
    ms.search.return_value = []
    settings = MemorySettings(search_l2_quota=7, search_l3_quota=4)
    engine = DefaultContextEngine(memory_store=ms, memory_settings=settings)

    await engine.assemble(
        session_id="feishu:a:s:x",
        messages=[],
        prompt="some query",
    )

    ms.search.assert_awaited_once_with(
        "feishu:a",
        "some query",
        layers=["L2", "L3"],
        per_layer_limits={"L2": 7, "L3": 4},
    )


async def test_assemble_passes_per_layer_limits_to_search() -> None:
    ms = AsyncMock()
    ms.search.return_value = []
    settings = MemorySettings(search_l2_quota=3, search_l3_quota=2)
    engine = DefaultContextEngine(memory_store=ms, memory_settings=settings)

    await engine.assemble(
        session_id="web:bob:s:y",
        messages=[],
        prompt="how to deploy?",
    )

    call_kwargs = ms.search.await_args.kwargs
    assert call_kwargs["per_layer_limits"] == {"L2": 3, "L3": 2}
    assert call_kwargs["layers"] == ["L2", "L3"]
    assert "limit" not in call_kwargs


async def test_assemble_without_memory_settings_uses_defaults() -> None:
    ms = AsyncMock()
    ms.search.return_value = []
    engine = DefaultContextEngine(memory_store=ms)

    await engine.assemble(
        session_id="x:s:y",
        messages=[],
        prompt="anything",
    )

    call_kwargs = ms.search.await_args.kwargs
    assert call_kwargs["per_layer_limits"] == {"L2": 3, "L3": 2}


async def test_format_memory_context_partitions_by_layer() -> None:
    engine = DefaultContextEngine()

    results = [
        _entry("l2-1", "user prefers concise answers", layer="L2", type_="user_preference"),
        _entry("l2-2", "redis at localhost:6379", layer="L2", type_="env_fact"),
        _entry("l3-1", "deploy: tag then push", layer="L3", type_="workflow"),
    ]

    output = engine._format_memory_context(results)

    assert output is not None
    assert "<memory_context>" in output
    assert "</memory_context>" in output
    assert "<facts>" in output
    assert "</facts>" in output
    assert "<procedures>" in output
    assert "</procedures>" in output

    facts_start = output.index("<facts>")
    facts_end = output.index("</facts>")
    facts_block = output[facts_start:facts_end]
    assert "user prefers concise answers" in facts_block
    assert "[user_preference]" in facts_block
    assert "redis at localhost:6379" in facts_block
    assert "[env_fact]" in facts_block
    assert "deploy: tag then push" not in facts_block

    procedures_start = output.index("<procedures>")
    procedures_end = output.index("</procedures>")
    procedures_block = output[procedures_start:procedures_end]
    assert "deploy: tag then push" in procedures_block
    assert "[workflow|" in procedures_block
    assert "user prefers concise answers" not in procedures_block

    assert facts_end < procedures_start


async def test_format_memory_context_only_l2_no_empty_procedures() -> None:
    engine = DefaultContextEngine()

    results = [
        _entry("l2-1", "fact only", layer="L2", type_="env_fact"),
    ]

    output = engine._format_memory_context(results)

    assert output is not None
    assert "<facts>" in output
    assert "<procedures>" not in output
    assert "fact only" in output


async def test_format_memory_context_only_l3_no_empty_facts() -> None:
    engine = DefaultContextEngine()

    results = [
        _entry("l3-1", "workflow only", layer="L3", type_="workflow"),
    ]

    output = engine._format_memory_context(results)

    assert output is not None
    assert "<procedures>" in output
    assert "<facts>" not in output
    assert "workflow only" in output


async def test_format_memory_context_empty_returns_none() -> None:
    engine = DefaultContextEngine()
    assert engine._format_memory_context([]) is None


async def test_assemble_produces_partitioned_format_in_system_prompt_addition() -> None:
    ms = AsyncMock()
    ms.search.return_value = [
        _entry("l2-1", "my fact", layer="L2", type_="env_fact"),
        _entry("l3-1", "my workflow", layer="L3", type_="workflow"),
    ]
    engine = DefaultContextEngine(memory_store=ms)

    result = await engine.assemble(
        session_id="x:s:y",
        messages=[],
        prompt="query",
    )

    assert result.system_prompt_addition is not None
    assert "<facts>" in result.system_prompt_addition
    assert "<procedures>" in result.system_prompt_addition
    assert "my fact" in result.system_prompt_addition
    assert "my workflow" in result.system_prompt_addition


async def test_assemble_custom_settings_from_env_var_style(monkeypatch) -> None:
    monkeypatch.setenv("PYCLAW_MEMORY_SEARCH_L2_QUOTA", "5")
    monkeypatch.setenv("PYCLAW_MEMORY_SEARCH_L3_QUOTA", "1")
    settings = MemorySettings()

    assert settings.search_l2_quota == 5
    assert settings.search_l3_quota == 1

    ms = AsyncMock()
    ms.search.return_value = []
    engine = DefaultContextEngine(memory_store=ms, memory_settings=settings)

    await engine.assemble(session_id="x:s:y", messages=[], prompt="q")

    call_kwargs = ms.search.await_args.kwargs
    assert call_kwargs["per_layer_limits"] == {"L2": 5, "L3": 1}
