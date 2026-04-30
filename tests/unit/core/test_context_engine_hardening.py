from __future__ import annotations

import asyncio

import pytest

from pyclaw.core.context_engine import DefaultContextEngine


def _big_conversation(pairs: int = 30) -> list[dict]:
    msgs = []
    for i in range(pairs):
        msgs.append({"role": "user", "content": f"question {i} with enough text " * 50})
        msgs.append({"role": "assistant", "content": f"answer {i} with enough text " * 50})
    return msgs


@pytest.mark.asyncio
async def test_compaction_succeeds_with_custom_summarizer() -> None:
    captured_models: list[str | None] = []

    async def _summarize(payload, *, model=None):
        captured_models.append(model)
        return "summary-text"

    engine = DefaultContextEngine(
        summarize=_summarize,
        keep_recent_tokens=100,
        chunk_token_budget=50_000,
    )

    result = await engine.compact(
        session_id="s1",
        messages=_big_conversation(),
        token_budget=1000,
        force=True,
        model="openai/gpt-4o-mini",
    )

    assert result.ok is True
    assert result.compacted is True
    assert result.reason_code == "compacted"
    assert result.summary is not None
    assert "openai/gpt-4o-mini" in captured_models


@pytest.mark.asyncio
async def test_compaction_summary_failed_sets_reason_code() -> None:
    async def _summarize(payload, *, model=None):
        raise RuntimeError("llm exploded")

    engine = DefaultContextEngine(summarize=_summarize, keep_recent_tokens=100)
    result = await engine.compact(
        session_id="s1",
        messages=_big_conversation(),
        token_budget=1000,
        force=True,
    )
    assert result.ok is False
    assert result.reason_code == "summary_failed"


@pytest.mark.asyncio
async def test_compaction_timeout_sets_reason_code() -> None:
    async def _summarize(payload, *, model=None):
        await asyncio.sleep(10)
        return "never"

    engine = DefaultContextEngine(
        summarize=_summarize,
        keep_recent_tokens=100,
        compaction_timeout_s=0.05,
    )
    result = await engine.compact(
        session_id="s1",
        messages=_big_conversation(),
        token_budget=1000,
        force=True,
    )
    assert result.ok is False
    assert result.reason_code == "timeout"


@pytest.mark.asyncio
async def test_compaction_skipped_when_no_real_conversation() -> None:
    engine = DefaultContextEngine()
    msgs = [
        {"role": "user", "content": "[heartbeat] ping"},
        {"role": "assistant", "content": "[heartbeat] pong"},
    ]
    result = await engine.compact(
        session_id="s1",
        messages=msgs,
        token_budget=1000,
    )
    assert result.compacted is False
    assert result.reason_code == "no_compactable_entries"


@pytest.mark.asyncio
async def test_tokens_after_clamped_when_estimate_bogus() -> None:
    async def _summarize(payload, *, model=None):
        return "x" * 500_000

    engine = DefaultContextEngine(summarize=_summarize, keep_recent_tokens=100)
    result = await engine.compact(
        session_id="s1",
        messages=_big_conversation(),
        token_budget=1000,
        force=True,
    )
    assert result.ok is True
    assert result.tokens_after is None
