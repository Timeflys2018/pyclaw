from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from pyclaw.core.agent.factory import create_agent_runner_deps
from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook
from pyclaw.core.agent.hooks.working_memory_hook import WorkingMemoryHook
from pyclaw.core.agent.llm import LLMClient, LLMStreamChunk, LLMUsage
from pyclaw.core.agent.runner import (
    AgentRunnerDeps,
    RunRequest,
    run_agent_stream,
)
from pyclaw.core.agent.system_prompt import PromptInputs, build_frozen_prefix
from pyclaw.core.agent.tools.memorize import MemorizeTool
from pyclaw.core.agent.tools.registry import ToolContext, ToolRegistry
from pyclaw.core.agent.tools.update_working_memory import UpdateWorkingMemoryTool
from pyclaw.core.context_engine import DefaultContextEngine
from pyclaw.core.hooks import HookRegistry
from pyclaw.infra.settings import Settings
from pyclaw.models import (
    AgentRunConfig,
    CompactionConfig,
    Done,
    ErrorEvent,
    MessageEntry,
    TextChunk,
    ToolCallEnd,
    now_iso,
)
from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.session.base import InMemorySessionStore


class _FakeRedis:
    def __init__(self) -> None:
        self._hashes: dict[str, dict[str, str]] = {}
        self._lists: dict[str, list[str]] = {}
        self._ttls: dict[str, int] = {}

    async def hget(self, key: str, field: str) -> str | None:
        return self._hashes.get(key, {}).get(field)

    async def hset(self, key: str, field: str, value: str) -> int:
        if key not in self._hashes:
            self._hashes[key] = {}
        is_new = field not in self._hashes[key]
        self._hashes[key][field] = value
        return 1 if is_new else 0

    async def hdel(self, key: str, field: str) -> int:
        h = self._hashes.get(key, {})
        if field in h:
            del h[field]
            return 1
        return 0

    async def hgetall(self, key: str) -> dict[str, str]:
        return dict(self._hashes.get(key, {}))

    async def rpush(self, key: str, value: str) -> int:
        if key not in self._lists:
            self._lists[key] = []
        self._lists[key].append(value)
        return len(self._lists[key])

    async def lpop(self, key: str) -> str | None:
        lst = self._lists.get(key)
        if not lst:
            return None
        return lst.pop(0)

    async def lrange(self, key: str, start: int, stop: int) -> list[str]:
        lst = self._lists.get(key, [])
        if stop == -1:
            return list(lst[start:])
        return list(lst[start : stop + 1])

    async def expire(self, key: str, seconds: int) -> int:
        self._ttls[key] = seconds
        return 1


class _FakeMemoryStore:
    def __init__(self) -> None:
        self.l1_by_key: dict[str, list[MemoryEntry]] = {}
        self.stored: list[tuple[str, MemoryEntry]] = []
        self.archive_calls: list[tuple[str, str, str]] = []
        self.search_results: list[MemoryEntry] = []

    async def index_get(self, session_key: str) -> list[MemoryEntry]:
        return list(self.l1_by_key.get(session_key, []))

    async def index_update(self, session_key: str, entry: MemoryEntry) -> None:
        self.l1_by_key.setdefault(session_key, []).append(entry)

    async def index_remove(self, session_key: str, entry_id: str) -> None:
        self.l1_by_key[session_key] = [
            e for e in self.l1_by_key.get(session_key, []) if e.id != entry_id
        ]

    async def search(
        self, session_key: str, query: str, *, layers=None, limit: int = 10, per_layer_limits=None
    ) -> list[MemoryEntry]:
        return list(self.search_results[:limit])

    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        self.stored.append((session_key, entry))
        self.l1_by_key.setdefault(session_key, []).append(entry)

    async def delete(self, session_key: str, entry_id: str) -> None:
        pass

    async def archive_session(self, session_key: str, session_id: str, summary: str) -> None:
        self.archive_calls.append((session_key, session_id, summary))

    async def search_archives(self, session_key: str, query: str, *, limit: int = 5, min_similarity: float = 0.0):
        return []

    async def close(self) -> None:
        pass


def _entry(eid: str, content: str, layer: str = "L2", type_: str = "fact") -> MemoryEntry:
    return MemoryEntry(
        id=eid,
        layer=layer,  # type: ignore[arg-type]
        type=type_,
        content=content,
        created_at=time.time(),
        updated_at=time.time(),
    )


def _ctx(session_id: str) -> ToolContext:
    return ToolContext(
        workspace_id="default",
        workspace_path=Path("/tmp"),
        session_id=session_id,
    )


async def _seed_session_with_tool_call(
    store: InMemorySessionStore, session_key: str
) -> str:
    tree = await store.create_new_session(session_key, "default", "default")
    session_id = tree.header.id
    u = MessageEntry(id="u1", parent_id=None, timestamp=now_iso(), role="user", content="do it")
    await store.append_entry(session_id, u, leaf_id=u.id)
    a = MessageEntry(
        id="a1", parent_id="u1", timestamp=now_iso(),
        role="assistant", content="calling tool",
        tool_calls=[{"id": "tc1", "function": {"name": "bash", "arguments": "{}"}}],
    )
    await store.append_entry(session_id, a, leaf_id=a.id)
    t = MessageEntry(id="t1", parent_id="a1", timestamp=now_iso(), role="tool", content="ok", tool_call_id="tc1")
    await store.append_entry(session_id, t, leaf_id=t.id)
    return session_id


async def test_memorize_in_session_a_appears_in_l1_snapshot_for_session_b() -> None:
    store = InMemorySessionStore()
    memory_store = _FakeMemoryStore()
    session_key = "feishu:cli:ou_abc"

    session_a = await _seed_session_with_tool_call(store, session_key)

    tool = MemorizeTool(memory_store, store)
    result = await tool.execute(
        {"content": "user prefers concise answers", "layer": "L2", "type": "user_preference"},
        _ctx(session_a),
    )
    assert not result.is_error
    assert len(memory_store.stored) == 1

    session_b = (await store.create_new_session(session_key, "default", "default")).header.id

    engine = DefaultContextEngine(memory_store=memory_store)
    entries = await engine.get_l1_snapshot(session_b)

    contents = [e.content for e in entries]
    assert "user prefers concise answers" in contents


async def test_update_working_memory_appears_in_prompt() -> None:
    redis = _FakeRedis()
    tool = UpdateWorkingMemoryTool(redis)
    hook = WorkingMemoryHook(redis)

    session_id = "feishu:cli:ou_alpha:s:abc"
    await tool.execute({"key": "current_goal", "value": "deploy v2"}, _ctx(session_id))

    from pyclaw.core.hooks import PromptBuildContext

    ctx = PromptBuildContext(session_id=session_id, workspace_id="default", agent_id="main")
    result = await hook.before_prompt_build(ctx)

    assert result is not None
    assert result.append is not None
    assert "<working_memory>" in result.append
    assert "current_goal" in result.append
    assert "deploy v2" in result.append


async def test_new_session_starts_with_empty_working_memory() -> None:
    redis = _FakeRedis()
    tool = UpdateWorkingMemoryTool(redis)
    hook = WorkingMemoryHook(redis)

    session_a = "web:alice:s:s1"
    session_b = "web:alice:s:s2"

    await tool.execute({"key": "note", "value": "session A data"}, _ctx(session_a))

    from pyclaw.core.hooks import PromptBuildContext

    ctx_b = PromptBuildContext(session_id=session_b, workspace_id="default", agent_id="main")
    result_b = await hook.before_prompt_build(ctx_b)
    assert result_b is None


async def test_memorize_without_tool_call_rejected() -> None:
    store = InMemorySessionStore()
    memory_store = _FakeMemoryStore()
    session_key = "feishu:cli:ou_no_tool"

    tree = await store.create_new_session(session_key, "default", "default")
    session_id = tree.header.id
    u = MessageEntry(id="u1", parent_id=None, timestamp=now_iso(), role="user", content="hi")
    await store.append_entry(session_id, u, leaf_id=u.id)

    tool = MemorizeTool(memory_store, store)
    result = await tool.execute(
        {"content": "something", "layer": "L2"}, _ctx(session_id)
    )
    assert result.is_error
    assert memory_store.stored == []


async def test_memorize_with_invalid_layer_rejected() -> None:
    store = InMemorySessionStore()
    memory_store = _FakeMemoryStore()
    session_id = await _seed_session_with_tool_call(store, "feishu:cli:ou_bad")

    tool = MemorizeTool(memory_store, store)
    result = await tool.execute(
        {"content": "something", "layer": "L1"}, _ctx(session_id)
    )
    assert result.is_error
    assert "L2" in result.content[0].text or "L3" in result.content[0].text
    assert memory_store.stored == []


async def test_agent_runs_normally_without_memory_store() -> None:
    settings = Settings()
    store = InMemorySessionStore()

    deps = await create_agent_runner_deps(settings, store)

    assert "memorize" not in deps.tools
    assert "update_working_memory" not in deps.tools

    hook_types = {type(h) for h in deps.hooks.hooks()}
    assert WorkingMemoryHook not in hook_types
    assert MemoryNudgeHook not in hook_types


async def test_bootstrap_appears_in_frozen_prefix_not_system_addition() -> None:
    from pyclaw.storage.workspace.file import FileWorkspaceStore
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        workspace_store = FileWorkspaceStore(base_dir=Path(tmpdir))
        await workspace_store.put_file(
            "feishu_cli_ou_test", "AGENTS.md", "bootstrap content here"
        )
        engine = DefaultContextEngine(workspace_store=workspace_store)

        session_id = "feishu:cli:ou_test:s:abc"

        bootstrap_text = await engine.get_bootstrap(session_id)
        assert bootstrap_text == "bootstrap content here"

        inputs = PromptInputs(
            session_id=session_id,
            workspace_id="feishu_cli_ou_test",
            agent_id="main",
            model="gpt-4o",
            tools=(),
        )
        frozen = build_frozen_prefix(inputs, budget=2048, bootstrap=bootstrap_text)

        assert "bootstrap content here" in frozen.text
        assert "bootstrap" in frozen.token_breakdown

        result = await engine.assemble(session_id=session_id, messages=[])
        assert result.system_prompt_addition is None


async def test_l2_l3_search_results_appear_in_system_prompt_addition() -> None:
    memory_store = _FakeMemoryStore()
    memory_store.search_results = [
        _entry("m1", "use Redis for L1 index", layer="L2", type_="env_fact"),
        _entry("m2", "deploy: tag release then push", layer="L3", type_="workflow"),
    ]

    engine = DefaultContextEngine(memory_store=memory_store)

    result = await engine.assemble(
        session_id="feishu:cli:ou_x:s:y",
        messages=[{"role": "user", "content": "how to deploy?"}],
        prompt="how to deploy?",
    )

    assert result.system_prompt_addition is not None
    assert "<memory_context>" in result.system_prompt_addition
    assert "<facts>" in result.system_prompt_addition
    assert "<procedures>" in result.system_prompt_addition
    assert "use Redis for L1 index" in result.system_prompt_addition
    assert "deploy: tag release then push" in result.system_prompt_addition


async def test_archive_session_fires_on_rotate() -> None:
    from pyclaw.core.memory_archive import archive_session_background

    store = InMemorySessionStore()
    memory_store = _FakeMemoryStore()

    session_key = "feishu:cli:ou_arch"
    tree = await store.create_new_session(session_key, "default", "default")
    session_id = tree.header.id
    prior = None
    for i in range(5):
        u = MessageEntry(
            id=f"u{i}", parent_id=prior, timestamp=now_iso(),
            role="user", content=f"user {i}",
        )
        await store.append_entry(session_id, u, leaf_id=u.id)
        prior = u.id
        a = MessageEntry(
            id=f"a{i}", parent_id=prior, timestamp=now_iso(),
            role="assistant", content=f"answer {i}",
        )
        await store.append_entry(session_id, a, leaf_id=a.id)
        prior = a.id

    await archive_session_background(memory_store, store, session_id)

    assert len(memory_store.archive_calls) == 1
    assert memory_store.archive_calls[0][0] == session_key
    assert memory_store.archive_calls[0][1] == session_id
    assert len(memory_store.archive_calls[0][2]) > 0


class _FakeLLM(LLMClient):
    def __init__(self, model: str = "fake", final_text: str = "done") -> None:
        super().__init__(default_model=model)
        self._final_text = final_text

    def stream(self, **kwargs):
        llm = self

        async def _gen():
            yield LLMStreamChunk(text_delta=llm._final_text)
            yield LLMStreamChunk(finish_reason="stop", usage=LLMUsage())

        return _gen()


async def test_full_agent_run_with_memory_pipeline_integrates_cleanly(tmp_path) -> None:
    store = InMemorySessionStore()
    memory_store = _FakeMemoryStore()
    memory_store.l1_by_key["web:alice"] = [_entry("m1", "alice prefers dark mode", layer="L2", type_="user_preference")]
    memory_store.search_results = [
        _entry("s1", "deploy: use github actions", layer="L3", type_="workflow"),
    ]

    settings = Settings()
    redis = _FakeRedis()

    deps = await create_agent_runner_deps(
        settings, store, memory_store=memory_store, redis_client=redis
    )
    deps = AgentRunnerDeps(
        llm=_FakeLLM(final_text="Here's how to deploy."),
        tools=deps.tools,
        context_engine=deps.context_engine,
        hooks=deps.hooks,
        session_store=store,
        config=deps.config,
        workspace_store=deps.workspace_store,
        skill_provider=deps.skill_provider,
        task_manager=deps.task_manager,
    )

    events: list[Any] = []
    async for evt in run_agent_stream(
        RunRequest(
            session_id="web:alice:s:integ1",
            workspace_id="default",
            agent_id="main",
            user_message="how to deploy?",
        ),
        deps,
        tool_workspace_path=tmp_path,
    ):
        events.append(evt)

    errors = [e for e in events if isinstance(e, ErrorEvent)]
    assert not errors, f"unexpected errors: {errors}"
    done = [e for e in events if isinstance(e, Done)]
    assert done
    assert "deploy" in done[0].final_message.lower()
