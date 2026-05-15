from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from pyclaw.core.agent.tools.registry import ToolContext
from pyclaw.core.agent.tools.update_working_memory import UpdateWorkingMemoryTool


def _ctx(session_id: str = "sess1") -> ToolContext:
    return ToolContext(workspace_id="default", workspace_path=Path("/tmp"), session_id=session_id)


def _make_redis(hash_data: dict | None = None, order_data: list | None = None):
    store: dict[str, dict[str, str]] = {}
    lists: dict[str, list[str]] = {}
    ttls: dict[str, int] = {}

    if hash_data:
        store["pyclaw:wm:sess1"] = dict(hash_data)
    if order_data:
        lists["pyclaw:wm:sess1:order"] = list(order_data)

    redis = AsyncMock()

    async def hset(key, field, value):
        store.setdefault(key, {})[field] = value

    async def hget(key, field):
        return store.get(key, {}).get(field)

    async def hgetall(key):
        return dict(store.get(key, {}))

    async def hdel(key, field):
        store.get(key, {}).pop(field, None)

    async def rpush(key, value):
        lists.setdefault(key, []).append(value)

    async def lpop(key):
        lst = lists.get(key, [])
        if lst:
            return lst.pop(0)
        return None

    async def expire(key, seconds):
        ttls[key] = seconds

    redis.hset = AsyncMock(side_effect=hset)
    redis.hget = AsyncMock(side_effect=hget)
    redis.hgetall = AsyncMock(side_effect=hgetall)
    redis.hdel = AsyncMock(side_effect=hdel)
    redis.rpush = AsyncMock(side_effect=rpush)
    redis.lpop = AsyncMock(side_effect=lpop)
    redis.expire = AsyncMock(side_effect=expire)
    redis._store = store
    redis._lists = lists
    redis._ttls = ttls
    return redis


class TestUpdateWorkingMemoryTool:
    async def test_writes_to_redis_and_readable_via_hgetall(self) -> None:
        redis = _make_redis()
        tool = UpdateWorkingMemoryTool(redis)
        result = await tool.execute(
            {"_call_id": "c1", "key": "user_name", "value": "Alice"}, _ctx()
        )

        assert not result.is_error
        assert "stored 'user_name'" in result.content[0].text
        assert redis._store["pyclaw:wm:sess1"]["user_name"] == "Alice"
        assert "user_name" in redis._lists["pyclaw:wm:sess1:order"]

    async def test_fifo_eviction_when_total_chars_exceeds_max(self) -> None:
        redis = _make_redis()
        tool = UpdateWorkingMemoryTool(redis, max_chars=15)

        await tool.execute({"_call_id": "c1", "key": "aaa", "value": "111"}, _ctx())
        await tool.execute({"_call_id": "c2", "key": "bbb", "value": "222"}, _ctx())
        await tool.execute({"_call_id": "c3", "key": "ccc", "value": "333"}, _ctx())

        data = redis._store["pyclaw:wm:sess1"]
        total = sum(len(k) + len(v) for k, v in data.items())
        assert total <= 15
        assert "aaa" not in data

    async def test_ttl_refreshed_on_write(self) -> None:
        redis = _make_redis()
        tool = UpdateWorkingMemoryTool(redis, ttl_seconds=604800)
        await tool.execute({"_call_id": "c1", "key": "k", "value": "v"}, _ctx())

        redis.expire.assert_any_call("pyclaw:wm:sess1", 604800)
        redis.expire.assert_any_call("pyclaw:wm:sess1:order", 604800)

    async def test_invalid_args_missing_key_returns_error(self) -> None:
        redis = _make_redis()
        tool = UpdateWorkingMemoryTool(redis)
        result = await tool.execute({"_call_id": "c1", "key": "", "value": "v"}, _ctx())

        assert result.is_error
        assert "non-empty string" in result.content[0].text

    async def test_empty_value_accepted(self) -> None:
        redis = _make_redis()
        tool = UpdateWorkingMemoryTool(redis)
        result = await tool.execute({"_call_id": "c1", "key": "flag", "value": ""}, _ctx())

        assert not result.is_error
        assert redis._store["pyclaw:wm:sess1"]["flag"] == ""

    async def test_update_existing_key_does_not_duplicate_in_order(self) -> None:
        redis = _make_redis()
        tool = UpdateWorkingMemoryTool(redis)
        await tool.execute({"_call_id": "c1", "key": "x", "value": "1"}, _ctx())
        await tool.execute({"_call_id": "c2", "key": "x", "value": "2"}, _ctx())

        assert redis._lists["pyclaw:wm:sess1:order"].count("x") == 1
        assert redis._store["pyclaw:wm:sess1"]["x"] == "2"

    async def test_redis_error_returns_error_result(self) -> None:
        redis = AsyncMock()
        redis.hget = AsyncMock(side_effect=ConnectionError("connection lost"))
        tool = UpdateWorkingMemoryTool(redis)
        result = await tool.execute({"_call_id": "c1", "key": "k", "value": "v"}, _ctx())

        assert result.is_error
        assert "update_working_memory:" in result.content[0].text
