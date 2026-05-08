"""Tests for trigger integration: maybe_spawn_extraction + after_compaction."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook
from pyclaw.core.agent.hooks.sop_tracker_hook import SopCandidateTracker
from pyclaw.core.hooks import CompactionContext
from pyclaw.core.sop_extraction import maybe_spawn_extraction
from pyclaw.infra.settings import EvolutionSettings
from pyclaw.models import CompactResult


def _settings(**kwargs):
    base = {"enabled": True, "min_tool_calls_for_extraction": 2}
    base.update(kwargs)
    return EvolutionSettings(**base)


def _candidate(turn_id: str, tool_names: list[str]) -> str:
    import json
    return json.dumps({
        "turn_id": turn_id,
        "user_msg": "test",
        "tool_names": tool_names,
        "timestamp": 1.0,
    })


def _redis(tool_calls_per_turn=None, lock_held=False):
    redis = MagicMock()
    if tool_calls_per_turn is None:
        tool_calls_per_turn = []
    entries = {
        f"call_{i}": _candidate(f"call_{i}", ["read"] * n)
        for i, n in enumerate(tool_calls_per_turn)
    }
    redis.hgetall = AsyncMock(return_value=entries)
    redis.hlen = AsyncMock(return_value=len(entries))
    redis.set = AsyncMock(return_value=None if lock_held else True)
    redis.delete = AsyncMock(return_value=1)
    return redis


def _task_manager():
    tm = MagicMock()

    def _spawn(name, coro, **kwargs):
        coro.close()
        return "t000001"

    tm.spawn = MagicMock(side_effect=_spawn)
    return tm


def _llm():
    llm = MagicMock()
    llm.complete = AsyncMock(return_value=MagicMock(text="[]"))
    return llm


class TestMaybeSpawnExtraction:
    @pytest.mark.asyncio
    async def test_spawn_when_tool_calls_above_threshold(self):
        tm = _task_manager()
        result = await maybe_spawn_extraction(
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            redis_client=_redis(tool_calls_per_turn=[1, 1, 1]),
            llm_client=_llm(),
            session_id="ses_1",
            settings=_settings(min_tool_calls_for_extraction=2),
        )
        assert result is True
        tm.spawn.assert_called_once()
        call_kwargs = tm.spawn.call_args
        assert call_kwargs.kwargs.get("category") == "evolution" or "evolution" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_no_spawn_when_below_threshold(self):
        tm = _task_manager()
        result = await maybe_spawn_extraction(
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            redis_client=_redis(tool_calls_per_turn=[1]),
            llm_client=_llm(),
            session_id="ses_1",
            settings=_settings(min_tool_calls_for_extraction=2),
        )
        assert result is False
        tm.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_spawn_when_single_turn_has_multiple_parallel_tools(self):
        tm = _task_manager()
        result = await maybe_spawn_extraction(
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            redis_client=_redis(tool_calls_per_turn=[3]),
            llm_client=_llm(),
            session_id="ses_1",
            settings=_settings(min_tool_calls_for_extraction=2),
        )
        assert result is True
        tm.spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_spawn_when_lock_held(self):
        tm = _task_manager()
        result = await maybe_spawn_extraction(
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            redis_client=_redis(tool_calls_per_turn=[5, 5], lock_held=True),
            llm_client=_llm(),
            session_id="ses_1",
            settings=_settings(),
        )
        assert result is False
        tm.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_spawn_when_disabled(self):
        tm = _task_manager()
        result = await maybe_spawn_extraction(
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            redis_client=_redis(tool_calls_per_turn=[5, 5]),
            llm_client=_llm(),
            session_id="ses_1",
            settings=_settings(enabled=False),
        )
        assert result is False
        tm.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_min_tool_calls_override_via_param(self):
        tm = _task_manager()
        result = await maybe_spawn_extraction(
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            redis_client=_redis(tool_calls_per_turn=[1]),
            llm_client=_llm(),
            session_id="ses_1",
            settings=_settings(min_tool_calls_for_extraction=2),
            min_tool_calls=1,
        )
        assert result is True
        tm.spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_malformed_candidate_counts_as_one(self):
        tm = _task_manager()
        redis = MagicMock()
        redis.hgetall = AsyncMock(return_value={"call_1": "not-json", "call_2": "{}"})
        redis.set = AsyncMock(return_value=True)
        redis.delete = AsyncMock(return_value=1)
        result = await maybe_spawn_extraction(
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            redis_client=redis,
            llm_client=_llm(),
            session_id="ses_1",
            settings=_settings(min_tool_calls_for_extraction=2),
        )
        assert result is True
        tm.spawn.assert_called_once()


class TestNudgeCounterReset:
    def test_reset_counter_method(self):
        nudge = MemoryNudgeHook(interval=10)
        nudge._counts["ses_1"] = 7
        nudge.reset_counter("ses_1")
        assert "ses_1" not in nudge._counts

    def test_reset_counter_for_unknown_session(self):
        nudge = MemoryNudgeHook(interval=10)
        nudge.reset_counter("never_seen")
        assert "never_seen" not in nudge._counts


class TestSopTrackerAfterCompaction:
    @pytest.mark.asyncio
    async def test_after_compaction_spawns_extraction_when_compacted(self):
        tm = _task_manager()
        redis = _redis(tool_calls_per_turn=[1, 1, 1, 1, 1])

        tracker = SopCandidateTracker(
            redis_client=redis,
            settings=_settings(),
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            llm_client=_llm(),
        )
        ctx = CompactionContext(session_id="ses_1", workspace_id="ws", agent_id="a")
        result = CompactResult(ok=True, compacted=True, reason="threshold")

        await tracker.after_compaction(ctx, result)
        tm.spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_after_compaction_no_spawn_when_not_compacted(self):
        tm = _task_manager()
        tracker = SopCandidateTracker(
            redis_client=_redis(tool_calls_per_turn=[1, 1, 1, 1, 1]),
            settings=_settings(),
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            llm_client=_llm(),
        )
        ctx = CompactionContext(session_id="ses_1", workspace_id="ws", agent_id="a")
        result = CompactResult(ok=True, compacted=False, reason="not-needed")

        await tracker.after_compaction(ctx, result)
        tm.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_compaction_no_spawn_when_disabled(self):
        tm = _task_manager()
        tracker = SopCandidateTracker(
            redis_client=_redis(tool_calls_per_turn=[1, 1, 1, 1, 1]),
            settings=_settings(enabled=False),
            task_manager=tm,
            memory_store=MagicMock(),
            session_store=MagicMock(),
            llm_client=_llm(),
        )
        ctx = CompactionContext(session_id="ses_1", workspace_id="ws", agent_id="a")
        result = CompactResult(ok=True, compacted=True, reason="t")

        await tracker.after_compaction(ctx, result)
        tm.spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_compaction_silent_when_deps_missing(self):
        tracker = SopCandidateTracker(
            redis_client=_redis(),
            settings=_settings(),
        )
        ctx = CompactionContext(session_id="ses_1", workspace_id="ws", agent_id="a")
        result = CompactResult(ok=True, compacted=True, reason="t")

        await tracker.after_compaction(ctx, result)


class TestSetnxLock:
    @pytest.mark.asyncio
    async def test_lock_prevents_concurrent_extraction(self):
        from pyclaw.core.sop_extraction import maybe_spawn_extraction

        tm = _task_manager()
        redis = MagicMock()
        redis.hgetall = AsyncMock(return_value={
            f"call_{i}": _candidate(f"call_{i}", ["read"]) for i in range(5)
        })
        set_results = iter([True, None])

        async def fake_set(*args, **kwargs):
            return next(set_results)

        redis.set = fake_set
        redis.delete = AsyncMock(return_value=1)

        r1 = await maybe_spawn_extraction(
            task_manager=tm, memory_store=MagicMock(), session_store=MagicMock(),
            redis_client=redis, llm_client=_llm(), session_id="ses_1",
            settings=_settings(),
        )
        r2 = await maybe_spawn_extraction(
            task_manager=tm, memory_store=MagicMock(), session_store=MagicMock(),
            redis_client=redis, llm_client=_llm(), session_id="ses_1",
            settings=_settings(),
        )

        assert r1 is True
        assert r2 is False
        assert tm.spawn.call_count == 1

    @pytest.mark.asyncio
    async def test_lock_released_after_success(self):
        from pyclaw.core.sop_extraction import _extract_then_reset

        redis = MagicMock()
        redis.delete = AsyncMock(return_value=1)
        redis.hgetall = AsyncMock(return_value={})

        session_store = MagicMock()
        session_store.load = AsyncMock(return_value=None)

        await _extract_then_reset(
            MagicMock(), session_store, redis, _llm(),
            "ses_1", _settings(), None, "pyclaw:sop_extracting:ses_1",
        )

        redis.delete.assert_any_call("pyclaw:sop_extracting:ses_1")

    @pytest.mark.asyncio
    async def test_lock_released_after_exception(self):
        from pyclaw.core.sop_extraction import _extract_then_reset

        redis = MagicMock()
        redis.delete = AsyncMock(return_value=1)
        redis.hgetall = AsyncMock(side_effect=RuntimeError("boom"))

        await _extract_then_reset(
            MagicMock(), MagicMock(), redis, _llm(),
            "ses_1", _settings(), None, "pyclaw:sop_extracting:ses_1",
        )

        redis.delete.assert_any_call("pyclaw:sop_extracting:ses_1")

    @pytest.mark.asyncio
    async def test_post_compaction_batch_extractable(self):
        from pyclaw.core.sop_extraction import maybe_spawn_extraction

        tm = _task_manager()
        redis = MagicMock()
        redis.set = AsyncMock(return_value=True)
        redis.delete = AsyncMock(return_value=1)

        batch_a = {f"call_{i}": _candidate(f"call_{i}", ["read"]) for i in range(5)}
        batch_b = {f"call_{i}": _candidate(f"call_{i}", ["read"]) for i in range(3)}
        hgetall_results = iter([batch_a, batch_b])

        async def fake_hgetall(*args):
            return next(hgetall_results)

        redis.hgetall = fake_hgetall

        r1 = await maybe_spawn_extraction(
            task_manager=tm, memory_store=MagicMock(), session_store=MagicMock(),
            redis_client=redis, llm_client=_llm(), session_id="ses_1",
            settings=_settings(),
        )
        r2 = await maybe_spawn_extraction(
            task_manager=tm, memory_store=MagicMock(), session_store=MagicMock(),
            redis_client=redis, llm_client=_llm(), session_id="ses_1",
            settings=_settings(),
        )

        assert r1 is True
        assert r2 is True
        assert tm.spawn.call_count == 2
