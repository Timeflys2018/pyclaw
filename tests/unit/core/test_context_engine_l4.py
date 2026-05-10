"""Tests for L4 archive retrieval integration in DefaultContextEngine.assemble.

Covers spec l4-retrieval scenarios:
- assemble invokes search_archives with correct params
- assemble skips search_archives when prompt is None or memory_store is None
- L4 exception does not block L2/L3 results (4-quadrant error isolation)
- L2/L3 exception does not block L4 results
- archives-only renders <memory_context> wrapper + <archives> section
- _format_memory_context signature breaking change is locked
"""

from __future__ import annotations

import logging
import time
from unittest.mock import AsyncMock

import pytest

from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.storage.memory.base import ArchiveEntry, MemoryEntry


def _l2_entry(content: str, type_: str = "user_preference") -> MemoryEntry:
    return MemoryEntry(
        id="l2-id",
        layer="L2",
        type=type_,
        content=content,
        created_at=time.time(),
        updated_at=time.time(),
    )


def _archive_entry(
    session_id: str,
    summary: str,
    similarity: float,
) -> ArchiveEntry:
    return ArchiveEntry(
        id="archive-id",
        session_id=session_id,
        summary=summary,
        created_at=time.time(),
        distance=1.0 - similarity,
        similarity=similarity,
    )


@pytest.mark.asyncio
async def test_assemble_invokes_search_archives_with_correct_params() -> None:
    """Spec scenario: L4 search invoked when memory_store and prompt both present."""
    ms = AsyncMock()
    ms.search.return_value = []
    ms.search_archives.return_value = []
    engine = DefaultContextEngine(memory_store=ms)

    await engine.assemble(
        session_id="web:admin:s:abc123",
        messages=[],
        prompt="hello world",
    )

    ms.search_archives.assert_called_once_with(
        "web:admin",
        "hello world",
        limit=5,
        min_similarity=0.5,
    )


@pytest.mark.asyncio
async def test_assemble_skips_search_archives_when_prompt_none() -> None:
    """Spec scenario: L4 search NOT invoked when prompt is None."""
    ms = AsyncMock()
    ms.search.return_value = []
    ms.search_archives.return_value = []
    engine = DefaultContextEngine(memory_store=ms)

    result = await engine.assemble(
        session_id="web:admin:s:abc123",
        messages=[],
        prompt=None,
    )

    ms.search.assert_not_called()
    ms.search_archives.assert_not_called()
    assert result.system_prompt_addition is None


@pytest.mark.asyncio
async def test_assemble_skips_search_archives_when_memory_store_none() -> None:
    """Spec scenario: L4 search NOT invoked when memory_store is None."""
    engine = DefaultContextEngine(memory_store=None)

    result = await engine.assemble(
        session_id="web:admin:s:abc123",
        messages=[],
        prompt="hello",
    )

    assert result.system_prompt_addition is None


@pytest.mark.asyncio
async def test_l4_exception_does_not_block_l2l3_results(caplog) -> None:
    """Spec scenario: L4 search exception does not block L2/L3 results."""
    ms = AsyncMock()
    ms.search.return_value = [_l2_entry("prefers concise")]
    ms.search_archives.side_effect = ConnectionError("redis down")
    engine = DefaultContextEngine(memory_store=ms)

    with caplog.at_level(logging.WARNING, logger="pyclaw.core.context_engine"):
        result = await engine.assemble(
            session_id="web:admin:s:abc",
            messages=[],
            prompt="hello",
        )

    assert result.system_prompt_addition is not None
    assert "<facts>" in result.system_prompt_addition
    assert "prefers concise" in result.system_prompt_addition
    assert "<archives>" not in result.system_prompt_addition

    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("search_archives" in m for m in warning_messages), \
        f"Expected warning about search_archives. Captured: {warning_messages}"


@pytest.mark.asyncio
async def test_l2l3_exception_does_not_block_l4_results(caplog) -> None:
    """Spec scenario: L2/L3 search exception does not block L4 results."""
    ms = AsyncMock()
    ms.search.side_effect = RuntimeError("fts down")
    ms.search_archives.return_value = [
        _archive_entry("web:admin:s:f8b9701e8f80cb8b", "user: hello", similarity=0.74),
    ]
    engine = DefaultContextEngine(memory_store=ms)

    with caplog.at_level(logging.WARNING, logger="pyclaw.core.context_engine"):
        result = await engine.assemble(
            session_id="web:admin:s:abc",
            messages=[],
            prompt="hello",
        )

    assert result.system_prompt_addition is not None
    assert "<archives>" in result.system_prompt_addition
    assert "<facts>" not in result.system_prompt_addition
    assert "<procedures>" not in result.system_prompt_addition
    assert "<memory_context>" in result.system_prompt_addition

    warning_messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("L2/L3" in m or "search " in m for m in warning_messages), \
        f"Expected warning about L2/L3 search. Captured: {warning_messages}"


@pytest.mark.asyncio
async def test_archives_only_renders_memory_context_wrapper() -> None:
    """Spec scenario: archives_results non-empty, L2/L3 empty -> renders only archives section."""
    ms = AsyncMock()
    ms.search.return_value = []
    ms.search_archives.return_value = [
        _archive_entry("web:admin:s:f8b9701e8f80cb8b", "user: hello\nassistant: hi", similarity=0.74),
        _archive_entry("web:admin:s:c7ffc612d041f700", "user: bye", similarity=0.32),
    ]
    engine = DefaultContextEngine(memory_store=ms)

    result = await engine.assemble(
        session_id="web:admin:s:abc",
        messages=[],
        prompt="hello",
    )

    assert result.system_prompt_addition is not None
    output = result.system_prompt_addition
    assert "<memory_context>" in output
    assert "</memory_context>" in output
    assert "<archives>" in output
    assert "</archives>" in output
    assert "<facts>" not in output
    assert "<procedures>" not in output
    assert "[session=f8b9701e8f80|sim=0.74]" in output
    assert "[session=c7ffc612d041|sim=0.32]" in output
    assert "user: hello\nassistant: hi" in output
    assert "user: bye" in output


def test_format_memory_context_old_signature_rejected() -> None:
    """Regression guard: D5 design decision — old single-arg call must raise TypeError.

    Locks the breaking change. If a future change accidentally adds a default
    archive_results=None parameter, this test fails — forcing review.
    """
    with pytest.raises(TypeError):
        DefaultContextEngine._format_memory_context([])  # type: ignore[call-arg]


# ============================================================================
# add-archive-toggle-and-per-turn-cache change — 9 new tests
#
# Mock pattern: AsyncMock for memory_store; explicit stub
#   `ms.search.return_value = []` + `ms.search_archives.return_value = [...]`
# Assertion via `assert_called_once()` / `.call_count == N` / `.assert_not_called()`.
# ============================================================================


from pyclaw.infra.settings import MemorySettings


@pytest.mark.asyncio
async def test_assemble_skips_search_archives_when_archive_disabled() -> None:
    """Spec scenario: archive_enabled=False causes search_archives skip."""
    ms = AsyncMock()
    ms.search.return_value = []
    ms.search_archives.return_value = []
    settings = MemorySettings(archive_enabled=False)
    engine = DefaultContextEngine(memory_store=ms, memory_settings=settings)

    result = await engine.assemble(
        session_id="web:admin:s:abc",
        messages=[],
        prompt="hello",
    )

    ms.search_archives.assert_not_called()
    assert result.system_prompt_addition is None or "<archives>" not in result.system_prompt_addition


@pytest.mark.asyncio
async def test_assemble_calls_search_archives_when_archive_enabled() -> None:
    """Spec scenario: archive_enabled=True invokes search_archives normally."""
    ms = AsyncMock()
    ms.search.return_value = []
    ms.search_archives.return_value = []
    settings = MemorySettings(archive_enabled=True)
    engine = DefaultContextEngine(memory_store=ms, memory_settings=settings)

    await engine.assemble(
        session_id="web:admin:s:abc",
        messages=[],
        prompt="hello",
    )

    ms.search_archives.assert_called_once()


@pytest.mark.parametrize(
    "env_value,expected",
    [
        ("false", False),
        ("False", False),
        ("0", False),
        ("no", False),
        ("true", True),
        (None, True),
    ],
)
def test_archive_enabled_from_env_var(monkeypatch, env_value: str | None, expected: bool) -> None:
    """Spec scenario: archive_enabled set via environment variable, with default fallback."""
    if env_value is None:
        monkeypatch.delenv("PYCLAW_MEMORY_ARCHIVE_ENABLED", raising=False)
    else:
        monkeypatch.setenv("PYCLAW_MEMORY_ARCHIVE_ENABLED", env_value)
    settings = MemorySettings()
    assert settings.archive_enabled is expected


@pytest.mark.asyncio
async def test_assemble_cache_hit_skips_embedding_call_within_turn() -> None:
    """Spec scenario: Cache hit within same user turn skips embedding API call."""
    ms = AsyncMock()
    ms.search.return_value = []
    cached_entry = _archive_entry("web:admin:s:f8b9701e8f80cb8b", "summary 1", similarity=0.74)
    ms.search_archives.return_value = [cached_entry]
    engine = DefaultContextEngine(memory_store=ms)

    result1 = await engine.assemble(session_id="s1", messages=[], prompt="hello")
    result2 = await engine.assemble(session_id="s1", messages=[], prompt="hello")

    assert ms.search_archives.call_count == 1
    assert "summary 1" in (result1.system_prompt_addition or "")
    assert "summary 1" in (result2.system_prompt_addition or "")


@pytest.mark.asyncio
async def test_assemble_cache_miss_when_prompt_changes() -> None:
    """Spec scenario: Cache miss when prompt changes for same session."""
    ms = AsyncMock()
    ms.search.return_value = []
    ms.search_archives.return_value = [_archive_entry("web:admin:s:abc", "any", similarity=0.6)]
    engine = DefaultContextEngine(memory_store=ms)

    await engine.assemble(session_id="s1", messages=[], prompt="prompt_one")
    await engine.assemble(session_id="s1", messages=[], prompt="prompt_two")

    assert ms.search_archives.call_count == 2


@pytest.mark.asyncio
async def test_after_turn_clears_session_cache() -> None:
    """Spec scenario: after_turn removes cache entry for the session."""
    ms = AsyncMock()
    ms.search.return_value = []
    ms.search_archives.return_value = [_archive_entry("web:admin:s:abc", "any", similarity=0.6)]
    engine = DefaultContextEngine(memory_store=ms)

    await engine.assemble(session_id="s1", messages=[], prompt="hello")
    assert "s1" in engine._archive_cache

    await engine.after_turn("s1", [])
    assert "s1" not in engine._archive_cache

    await engine.assemble(session_id="s1", messages=[], prompt="hello")
    assert ms.search_archives.call_count == 2


@pytest.mark.asyncio
async def test_archive_disabled_does_not_use_cache() -> None:
    """Spec scenario: archive_enabled=False does not read existing cache entries."""
    ms = AsyncMock()
    ms.search.return_value = []
    cached_entry = _archive_entry("web:admin:s:f8b9701e8f80cb8b", "stale data", similarity=0.74)
    ms.search_archives.return_value = [cached_entry]

    settings = MemorySettings(archive_enabled=True)
    engine = DefaultContextEngine(memory_store=ms, memory_settings=settings)
    await engine.assemble(session_id="s1", messages=[], prompt="hello")
    assert "s1" in engine._archive_cache
    cache_snapshot = dict(engine._archive_cache)

    engine._memory_settings = MemorySettings(archive_enabled=False)
    ms.search_archives.reset_mock()

    result = await engine.assemble(session_id="s1", messages=[], prompt="hello")

    ms.search_archives.assert_not_called()
    assert result.system_prompt_addition is None or "<archives>" not in result.system_prompt_addition
    assert engine._archive_cache == cache_snapshot


@pytest.mark.asyncio
async def test_fifo_eviction_at_cap(monkeypatch) -> None:
    """Spec scenario: FIFO eviction triggers at cap.

    Patches ARCHIVE_CACHE_MAX_ENTRIES to 3 to make the test fast and obvious.
    """
    from pyclaw.core import context_engine as ce_module
    monkeypatch.setattr(ce_module, "ARCHIVE_CACHE_MAX_ENTRIES", 3)

    ms = AsyncMock()
    ms.search.return_value = []
    ms.search_archives.return_value = [_archive_entry("web:admin:s:abc", "any", similarity=0.6)]
    engine = DefaultContextEngine(memory_store=ms)

    await engine.assemble(session_id="s1", messages=[], prompt="p1")
    await engine.assemble(session_id="s2", messages=[], prompt="p2")
    await engine.assemble(session_id="s3", messages=[], prompt="p3")
    assert len(engine._archive_cache) == 3
    assert "s1" in engine._archive_cache

    await engine.assemble(session_id="s4", messages=[], prompt="p4")

    assert len(engine._archive_cache) == 3
    assert "s1" not in engine._archive_cache
    assert "s4" in engine._archive_cache


@pytest.mark.asyncio
async def test_cache_isolation_across_sessions() -> None:
    """Spec scenario: cache isolation across different sessions."""
    ms = AsyncMock()
    ms.search.return_value = []
    ms.search_archives.return_value = [_archive_entry("web:admin:s:abc", "any", similarity=0.6)]
    engine = DefaultContextEngine(memory_store=ms)

    await engine.assemble(session_id="s1", messages=[], prompt="hello")
    await engine.assemble(session_id="s2", messages=[], prompt="hello")

    assert ms.search_archives.call_count == 2
    assert "s1" in engine._archive_cache
    assert "s2" in engine._archive_cache
