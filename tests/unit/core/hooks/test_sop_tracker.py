"""Tests for SopCandidateTracker hook."""

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.agent.hooks.sop_tracker_hook import SopCandidateTracker
from pyclaw.core.hooks import (
    CompactionContext,
    PromptBuildContext,
    ResponseObservation,
)
from pyclaw.infra.settings import EvolutionSettings
from pyclaw.models import CompactResult


def _mock_redis():
    redis = MagicMock()
    redis.hset = AsyncMock(return_value=1)
    redis.hlen = AsyncMock(return_value=0)
    redis.hgetall = AsyncMock(return_value={})
    redis.hdel = AsyncMock(return_value=0)
    return redis


def _settings(**overrides) -> EvolutionSettings:
    base = {"enabled": True, "max_candidates": 100}
    base.update(overrides)
    return EvolutionSettings(**base)


def _make_observation(session_id: str, tool_calls: list | None = None) -> ResponseObservation:
    return ResponseObservation(
        session_id=session_id,
        assistant_text="response",
        tool_calls=tool_calls or [],
    )


def _make_context(session_id: str, prompt: str = "") -> PromptBuildContext:
    return PromptBuildContext(
        session_id=session_id,
        workspace_id="ws_test",
        agent_id="default",
        available_tools=[],
        prompt=prompt,
    )


class TestSopCandidateTracker:
    @pytest.mark.asyncio
    async def test_after_response_with_tool_calls_stores_candidate(self):
        redis = _mock_redis()
        tracker = SopCandidateTracker(redis, _settings())

        await tracker.before_prompt_build(_make_context("ses_1", prompt="deploy app to k8s"))

        obs = _make_observation(
            "ses_1",
            tool_calls=[
                {"id": "call_abc", "function": {"name": "bash"}},
                {"id": "call_def", "function": {"name": "read"}},
            ],
        )
        await tracker.after_response(obs)

        redis.hset.assert_called_once()
        args = redis.hset.call_args[0]
        assert args[0] == "pyclaw:sop_candidates:ses_1"
        assert args[1] == "call_abc"
        candidate = json.loads(args[2])
        assert candidate["turn_id"] == "call_abc"
        assert candidate["user_msg"] == "deploy app to k8s"
        assert candidate["tool_names"] == ["bash", "read"]
        assert isinstance(candidate["timestamp"], float)

    @pytest.mark.asyncio
    async def test_after_response_with_empty_tool_calls_does_nothing(self):
        redis = _mock_redis()
        tracker = SopCandidateTracker(redis, _settings())

        obs = _make_observation("ses_1", tool_calls=[])
        await tracker.after_response(obs)

        redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_response_with_disabled_does_nothing(self):
        redis = _mock_redis()
        tracker = SopCandidateTracker(redis, _settings(enabled=False))

        obs = _make_observation(
            "ses_1",
            tool_calls=[{"id": "c1", "function": {"name": "bash"}}],
        )
        await tracker.after_response(obs)

        redis.hset.assert_not_called()

    @pytest.mark.asyncio
    async def test_fifo_eviction_when_over_max(self):
        oldest_time = time.time() - 100
        existing = {
            "old_1": json.dumps({"turn_id": "old_1", "timestamp": oldest_time}),
            "old_2": json.dumps({"turn_id": "old_2", "timestamp": oldest_time + 1}),
            "old_3": json.dumps({"turn_id": "old_3", "timestamp": oldest_time + 2}),
            "old_4": json.dumps({"turn_id": "old_4", "timestamp": oldest_time + 3}),
            "old_5": json.dumps({"turn_id": "old_5", "timestamp": oldest_time + 4}),
        }
        redis = _mock_redis()
        redis.hlen = AsyncMock(return_value=5)
        redis.hgetall = AsyncMock(return_value=existing)

        settings = _settings(max_candidates=3)
        tracker = SopCandidateTracker(redis, settings)
        tracker.EVICTION_BATCH = 2

        await tracker.before_prompt_build(_make_context("ses_1", prompt="task"))
        obs = _make_observation(
            "ses_1",
            tool_calls=[{"id": "new_call", "function": {"name": "bash"}}],
        )
        await tracker.after_response(obs)

        redis.hdel.assert_called_once()
        deleted_args = redis.hdel.call_args[0]
        assert deleted_args[0] == "pyclaw:sop_candidates:ses_1"
        deleted_fields = set(deleted_args[1:])
        assert deleted_fields == {"old_1", "old_2"}

    @pytest.mark.asyncio
    async def test_no_eviction_when_under_max(self):
        redis = _mock_redis()
        redis.hlen = AsyncMock(return_value=50)
        tracker = SopCandidateTracker(redis, _settings(max_candidates=100))

        await tracker.before_prompt_build(_make_context("ses_1", prompt="task"))
        obs = _make_observation(
            "ses_1",
            tool_calls=[{"id": "c1", "function": {"name": "bash"}}],
        )
        await tracker.after_response(obs)

        redis.hgetall.assert_not_called()
        redis.hdel.assert_not_called()

    @pytest.mark.asyncio
    async def test_candidate_user_msg_truncated_to_200(self):
        redis = _mock_redis()
        tracker = SopCandidateTracker(redis, _settings())

        long_prompt = "x" * 500
        await tracker.before_prompt_build(_make_context("ses_1", prompt=long_prompt))
        obs = _make_observation(
            "ses_1",
            tool_calls=[{"id": "c1", "function": {"name": "bash"}}],
        )
        await tracker.after_response(obs)

        candidate = json.loads(redis.hset.call_args[0][2])
        assert len(candidate["user_msg"]) == 200

    @pytest.mark.asyncio
    async def test_stub_methods_return_none(self):
        redis = _mock_redis()
        tracker = SopCandidateTracker(redis, _settings())

        ctx = CompactionContext(session_id="ses_1", workspace_id="ws", agent_id="default")
        result = CompactResult(ok=True, compacted=True, reason="manual")
        assert await tracker.before_compaction(ctx) is None
        assert await tracker.after_compaction(ctx, result) is None

    @pytest.mark.asyncio
    async def test_redis_failure_does_not_propagate(self):
        redis = _mock_redis()
        redis.hset = AsyncMock(side_effect=ConnectionError("redis down"))
        tracker = SopCandidateTracker(redis, _settings())

        await tracker.before_prompt_build(_make_context("ses_1", prompt="task"))
        obs = _make_observation(
            "ses_1",
            tool_calls=[{"id": "c1", "function": {"name": "bash"}}],
        )
        result = await tracker.after_response(obs)
        assert result is None

    @pytest.mark.asyncio
    async def test_synthetic_turn_id_when_no_call_id(self):
        redis = _mock_redis()
        tracker = SopCandidateTracker(redis, _settings())

        await tracker.before_prompt_build(_make_context("ses_1", prompt="task"))
        obs = _make_observation(
            "ses_1",
            tool_calls=[{"function": {"name": "bash"}}],
        )
        await tracker.after_response(obs)

        candidate = json.loads(redis.hset.call_args[0][2])
        assert candidate["turn_id"].startswith("turn_")


class TestCleanupSession:
    def test_cleanup_session_removes_entry(self):
        tracker = SopCandidateTracker(_mock_redis(), _settings())
        tracker._last_user_msg["ses_1"] = "deploy app"
        tracker.cleanup_session("ses_1")
        assert "ses_1" not in tracker._last_user_msg

    def test_cleanup_session_idempotent(self):
        tracker = SopCandidateTracker(_mock_redis(), _settings())
        tracker.cleanup_session("never_seen")
        assert "never_seen" not in tracker._last_user_msg

    @pytest.mark.asyncio
    async def test_after_compaction_calls_cleanup_session(self):
        redis = _mock_redis()
        tracker = SopCandidateTracker(redis, _settings(enabled=False))
        tracker._last_user_msg["ses_1"] = "hello"

        ctx = CompactionContext(session_id="ses_1", workspace_id="ws", agent_id="default")
        result = CompactResult(ok=True, compacted=False, reason="test")
        await tracker.after_compaction(ctx, result)

        assert "ses_1" not in tracker._last_user_msg
