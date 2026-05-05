from __future__ import annotations

from unittest.mock import AsyncMock

from pyclaw.core.agent.hooks.working_memory_hook import WorkingMemoryHook
from pyclaw.core.hooks import PromptBuildContext


def _context(session_id: str = "sess1") -> PromptBuildContext:
    return PromptBuildContext(
        session_id=session_id,
        workspace_id="default",
        agent_id="agent1",
    )


def _make_redis(hash_data: dict | None = None, order_data: list | None = None):
    redis = AsyncMock()

    async def hgetall(key):
        if hash_data is None:
            return {}
        return dict(hash_data)

    async def lrange(key, start, end):
        if order_data is None:
            return []
        return list(order_data)

    redis.hgetall = AsyncMock(side_effect=hgetall)
    redis.lrange = AsyncMock(side_effect=lrange)
    return redis


class TestWorkingMemoryHook:
    async def test_returns_none_when_wm_empty(self) -> None:
        redis = _make_redis(hash_data={})
        hook = WorkingMemoryHook(redis)
        result = await hook.before_prompt_build(_context())
        assert result is None

    async def test_returns_prompt_build_result_with_xml(self) -> None:
        redis = _make_redis(
            hash_data={"user": "Alice", "goal": "write tests"},
            order_data=["user", "goal"],
        )
        hook = WorkingMemoryHook(redis)
        result = await hook.before_prompt_build(_context())

        assert result is not None
        assert "<working_memory>" in result.append
        assert "- user: Alice" in result.append
        assert "- goal: write tests" in result.append
        assert "</working_memory>" in result.append

    async def test_multiple_entries_formatted_with_dash_bullets(self) -> None:
        redis = _make_redis(
            hash_data={"a": "1", "b": "2", "c": "3"},
            order_data=["a", "b", "c"],
        )
        hook = WorkingMemoryHook(redis)
        result = await hook.before_prompt_build(_context())

        lines = result.append.strip().split("\n")
        assert lines[0] == "<working_memory>"
        assert lines[1] == "- a: 1"
        assert lines[2] == "- b: 2"
        assert lines[3] == "- c: 3"
        assert lines[4] == "</working_memory>"

    async def test_truncation_drops_oldest_when_block_exceeds_max_chars(self) -> None:
        long_value = "x" * 500
        redis = _make_redis(
            hash_data={"old": long_value, "new": "short"},
            order_data=["old", "new"],
        )
        hook = WorkingMemoryHook(redis, max_chars=100)
        result = await hook.before_prompt_build(_context())

        assert result is not None
        assert "old" not in result.append
        assert "- new: short" in result.append

    async def test_redis_error_returns_none(self) -> None:
        redis = AsyncMock()
        redis.hgetall = AsyncMock(side_effect=ConnectionError("down"))
        hook = WorkingMemoryHook(redis)
        result = await hook.before_prompt_build(_context())
        assert result is None

    async def test_after_response_is_noop(self) -> None:
        redis = _make_redis()
        hook = WorkingMemoryHook(redis)
        from pyclaw.core.hooks import ResponseObservation

        obs = ResponseObservation(session_id="s", assistant_text="hi")
        result = await hook.after_response(obs)
        assert result is None

    async def test_before_compaction_is_noop(self) -> None:
        redis = _make_redis()
        hook = WorkingMemoryHook(redis)
        from pyclaw.core.hooks import CompactionContext

        ctx = CompactionContext(session_id="s", workspace_id="w", agent_id="a")
        result = await hook.before_compaction(ctx)
        assert result is None

    async def test_after_compaction_is_noop(self) -> None:
        redis = _make_redis()
        hook = WorkingMemoryHook(redis)
        from pyclaw.core.hooks import CompactionContext
        from pyclaw.models import CompactResult

        ctx = CompactionContext(session_id="s", workspace_id="w", agent_id="a")
        cr = CompactResult(ok=True, compacted=True, summary="sum", tokens_before=100, tokens_after=50)
        result = await hook.after_compaction(ctx, cr)
        assert result is None
