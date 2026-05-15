"""End-to-end integration tests for self-evolution SOP extraction (Change 4a).

Tests the full data flow:
  agent turn → SopCandidateTracker writes candidate → session rotates →
  on_session_rotated → maybe_spawn_extraction → extract_sop_background →
  L3 entry written → next session search finds it
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.agent.hooks.sop_tracker_hook import SopCandidateTracker
from pyclaw.core.hooks import (
    CompactionContext,
    PromptBuildContext,
    ResponseObservation,
)
from pyclaw.core.sop_extraction import extract_sop_background
from pyclaw.infra.settings import EvolutionSettings
from pyclaw.models import CompactResult, MessageEntry, SessionHeader, SessionTree
from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend

# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sqlite_memory(tmp_path: Path) -> SqliteMemoryBackend:
    """Real SqliteMemoryBackend writing to tmp_path."""
    return SqliteMemoryBackend(base_dir=tmp_path)


@pytest.fixture
def fake_redis() -> MagicMock:
    """Lightweight fake redis with hash + key/value support.

    Uses dicts for predictable behavior without external deps.
    """
    state_hashes: dict[str, dict[str, str]] = {}
    state_keys: dict[str, str] = {}

    redis = MagicMock()

    async def hset(key: str, field: str, value: str) -> int:
        state_hashes.setdefault(key, {})[field] = value
        return 1

    async def hgetall(key: str) -> dict[str, str]:
        return state_hashes.get(key, {})

    async def hlen(key: str) -> int:
        return len(state_hashes.get(key, {}))

    async def hdel(key: str, *fields: str) -> int:
        h = state_hashes.get(key, {})
        deleted = 0
        for f in fields:
            if f in h:
                del h[f]
                deleted += 1
        return deleted

    async def get(key: str) -> str | None:
        return state_keys.get(key)

    async def set_(key: str, value: str, ex: int | None = None, nx: bool = False) -> bool | None:
        if nx and key in state_keys:
            return None
        state_keys[key] = value
        return True

    async def delete(*keys: str) -> int:
        deleted = 0
        for k in keys:
            if k in state_hashes:
                del state_hashes[k]
                deleted += 1
            if k in state_keys:
                del state_keys[k]
                deleted += 1
        return deleted

    redis.hset = hset
    redis.hgetall = hgetall
    redis.hlen = hlen
    redis.hdel = hdel
    redis.get = get
    redis.set = set_
    redis.delete = delete
    return redis


@pytest.fixture
def evolution_settings() -> EvolutionSettings:
    return EvolutionSettings(
        enabled=True,
        min_tool_calls_for_extraction=2,
        max_candidates=100,
        dedup_overlap_threshold=0.6,
        max_sops_per_extraction=5,
    )


def _build_session_tree(session_id: str, turns: list[tuple[str, str, str]]) -> SessionTree:
    """Build a SessionTree from a list of (turn_id, user_msg, tool_name) tuples."""
    header = SessionHeader(
        id=session_id,
        session_key=session_id.split(":s:")[0],
        agent_id="default",
        workspace_id="ws_test",
    )
    tree = SessionTree(header=header)

    parent = None
    for i, (turn_id, user_msg, tool_name) in enumerate(turns):
        user_entry = MessageEntry(
            id=f"m_user_{i}",
            parent_id=parent,
            role="user",
            content=user_msg,
        )
        assistant_entry = MessageEntry(
            id=f"m_assistant_{i}",
            parent_id=user_entry.id,
            role="assistant",
            content=f"I'll handle turn {i}.",
            tool_calls=[
                {
                    "id": turn_id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": "{}"},
                },
            ],
        )
        tool_entry = MessageEntry(
            id=f"m_tool_{i}",
            parent_id=assistant_entry.id,
            role="tool",
            content=f"Tool {tool_name} output for turn {i}",
            tool_call_id=turn_id,
        )
        for entry in (user_entry, assistant_entry, tool_entry):
            tree.entries[entry.id] = entry
            tree.order.append(entry.id)
        parent = tool_entry.id

    tree.leaf_id = parent
    return tree


def _mock_session_store(tree: SessionTree | None) -> MagicMock:
    store = MagicMock()
    store.load = AsyncMock(return_value=tree)
    return store


def _mock_llm(response_text: str = "[]") -> MagicMock:
    llm = MagicMock()
    response = MagicMock()
    response.text = response_text
    llm.complete = AsyncMock(return_value=response)
    return llm


# ============================================================================
# Tests
# ============================================================================


class TestSelfEvolutionE2E:
    """End-to-end tests for the self-evolution SOP extraction pipeline."""

    @pytest.mark.asyncio
    async def test_full_flow_session_with_5_tool_calls_writes_l3(
        self,
        sqlite_memory: SqliteMemoryBackend,
        fake_redis: MagicMock,
        evolution_settings: EvolutionSettings,
    ) -> None:
        """7.1: simulate session with 5 tool_calls, rotate, verify L3 has auto_sop."""
        session_id = "user1:s:ses_a"
        session_key = "user1"

        # 1. Tracker records 5 candidates across turns
        tracker = SopCandidateTracker(fake_redis, evolution_settings)
        for i in range(5):
            await tracker.before_prompt_build(
                PromptBuildContext(
                    session_id=session_id,
                    workspace_id="ws_test",
                    agent_id="default",
                    available_tools=[],
                    prompt=f"Deploy iteration {i}",
                )
            )
            await tracker.after_response(
                ResponseObservation(
                    session_id=session_id,
                    assistant_text="ok",
                    tool_calls=[
                        {"id": f"call_{i}", "function": {"name": "bash"}},
                    ],
                )
            )

        assert await fake_redis.hlen(f"pyclaw:sop_candidates:{session_id}") == 5

        # 2. Build session tree matching the candidate turn_ids
        turns = [(f"call_{i}", f"Deploy iteration {i}", "bash") for i in range(5)]
        tree = _build_session_tree(session_id, turns)
        session_store = _mock_session_store(tree)

        # 3. Mock LLM returns one SOP
        llm_response = json.dumps(
            [
                {
                    "name": "deploy-flow",
                    "description": "deploy iteration workflow",
                    "procedure": "1. build 2. push 3. apply",
                }
            ]
        )
        llm = _mock_llm(llm_response)

        # 4. Trigger extraction (simulating on_session_rotated)
        await extract_sop_background(
            sqlite_memory,
            session_store,
            fake_redis,
            llm,
            session_id,
            evolution_settings,
        )

        # 5. Verify L3 has the auto_sop
        results = await sqlite_memory.search(
            session_key,
            "deploy",
            layers=["L3"],
        )
        assert len(results) >= 1
        assert any(r.type == "auto_sop" for r in results)
        # Cleanup happened — candidates deleted is the "already processed" signal
        assert await fake_redis.hlen(f"pyclaw:sop_candidates:{session_id}") == 0

    @pytest.mark.asyncio
    async def test_two_similar_sessions_second_finds_first_sop(
        self,
        sqlite_memory: SqliteMemoryBackend,
        fake_redis: MagicMock,
        evolution_settings: EvolutionSettings,
    ) -> None:
        """7.2: simulate 2 similar sessions, verify session 2's search finds session 1's SOP."""
        session_key = "user1"
        session1_id = "user1:s:ses_first"

        # Session 1: extract a deploy SOP
        tracker = SopCandidateTracker(fake_redis, evolution_settings)
        for i in range(3):
            await tracker.before_prompt_build(
                PromptBuildContext(
                    session_id=session1_id,
                    workspace_id="ws",
                    agent_id="a",
                    available_tools=[],
                    prompt=f"Deploy app step {i}",
                )
            )
            await tracker.after_response(
                ResponseObservation(
                    session_id=session1_id,
                    assistant_text="ok",
                    tool_calls=[{"id": f"call_{i}", "function": {"name": "bash"}}],
                )
            )

        turns = [(f"call_{i}", f"Deploy app step {i}", "bash") for i in range(3)]
        tree = _build_session_tree(session1_id, turns)
        session_store = _mock_session_store(tree)
        llm = _mock_llm(
            json.dumps(
                [
                    {
                        "name": "deploy-app",
                        "description": "deploy app workflow",
                        "procedure": "1. build container 2. push registry 3. apply manifest",
                    }
                ]
            )
        )
        await extract_sop_background(
            sqlite_memory,
            session_store,
            fake_redis,
            llm,
            session1_id,
            evolution_settings,
        )

        # Session 2 (different session_id, same session_key): search L3
        results = await sqlite_memory.search(
            session_key,
            "deploy app",
            layers=["L3"],
        )
        assert len(results) >= 1
        assert "deploy" in results[0].content.lower()
        # Verify use_count was bumped (dead column fix)
        assert results[0].use_count >= 1

    @pytest.mark.asyncio
    async def test_use_count_bumps_on_repeated_search(
        self,
        sqlite_memory: SqliteMemoryBackend,
        fake_redis: MagicMock,
        evolution_settings: EvolutionSettings,
    ) -> None:
        """7.3: verify dead-column-fix actually works — search bumps use_count."""
        session_key = "user1"

        # Manually insert an L3 entry
        entry = MemoryEntry(
            id=str(uuid.uuid4()),
            layer="L3",
            type="auto_sop",
            content="deploy kubernetes application via helm chart",
            source_session_id="ses_test",
            created_at=time.time(),
            updated_at=time.time(),
        )
        await sqlite_memory.store(session_key, entry)

        # First search
        r1 = await sqlite_memory.search(session_key, "deploy kubernetes", layers=["L3"])
        assert len(r1) >= 1
        first_count = r1[0].use_count

        # Second search
        r2 = await sqlite_memory.search(session_key, "deploy kubernetes", layers=["L3"])
        assert len(r2) >= 1
        assert r2[0].use_count > first_count

    @pytest.mark.asyncio
    async def test_pure_chat_no_tool_calls_no_extraction(
        self,
        sqlite_memory: SqliteMemoryBackend,
        fake_redis: MagicMock,
        evolution_settings: EvolutionSettings,
    ) -> None:
        """7.4: pure-chat session (0 tool_calls) → no candidates → extraction skipped."""
        session_id = "user1:s:chat_only"
        session_store = _mock_session_store(None)
        llm = _mock_llm("[]")

        # No candidates were recorded — call extract directly to confirm skip
        await extract_sop_background(
            sqlite_memory,
            session_store,
            fake_redis,
            llm,
            session_id,
            evolution_settings,
        )

        # No candidates means early return — no LLM call, no L3 entries
        llm.complete.assert_not_called()
        results = await sqlite_memory.search("user1", "anything", layers=["L3"])
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_feature_flag_disabled_silences_pipeline(
        self,
        sqlite_memory: SqliteMemoryBackend,
        fake_redis: MagicMock,
    ) -> None:
        """7.5: enabled=False → no candidates recorded by tracker."""
        disabled_settings = EvolutionSettings(enabled=False)
        tracker = SopCandidateTracker(fake_redis, disabled_settings)

        await tracker.after_response(
            ResponseObservation(
                session_id="user1:s:disabled",
                assistant_text="ok",
                tool_calls=[{"id": "c1", "function": {"name": "bash"}}],
            )
        )

        # No candidates written
        assert await fake_redis.hlen("pyclaw:sop_candidates:user1:s:disabled") == 0

    @pytest.mark.asyncio
    async def test_compaction_trigger_spawns_extraction(
        self,
        sqlite_memory: SqliteMemoryBackend,
        fake_redis: MagicMock,
        evolution_settings: EvolutionSettings,
    ) -> None:
        """after_compaction trigger fires extraction via SopCandidateTracker."""
        session_id = "user1:s:compact"

        # Pre-populate candidates
        for i in range(3):
            await fake_redis.hset(
                f"pyclaw:sop_candidates:{session_id}",
                f"c_{i}",
                json.dumps({"turn_id": f"c_{i}", "timestamp": time.time()}),
            )

        # Build matching tree
        turns = [(f"c_{i}", f"Task {i}", "read") for i in range(3)]
        tree = _build_session_tree(session_id, turns)
        session_store = _mock_session_store(tree)
        llm = _mock_llm(
            json.dumps(
                [
                    {
                        "name": "task-flow",
                        "description": "task workflow",
                        "procedure": "1. read 2. process",
                    }
                ]
            )
        )

        task_manager = MagicMock()

        # Track if spawn was called
        spawned: list[tuple[str, str | None]] = []

        def fake_spawn(name: str, coro: object, **kwargs: object) -> str:
            spawned.append((name, kwargs.get("category")))
            if hasattr(coro, "close"):
                coro.close()
            return "t000001"

        task_manager.spawn = fake_spawn

        tracker = SopCandidateTracker(
            fake_redis,
            evolution_settings,
            task_manager=task_manager,
            memory_store=sqlite_memory,
            session_store=session_store,
            llm_client=llm,
        )

        ctx = CompactionContext(
            session_id=session_id,
            workspace_id="ws",
            agent_id="a",
        )
        result = CompactResult(ok=True, compacted=True, reason="threshold")

        await tracker.after_compaction(ctx, result)

        assert len(spawned) == 1
        assert spawned[0][1] == "evolution"
