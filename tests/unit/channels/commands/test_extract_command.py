from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.channels.feishu.commands import handle_command
from pyclaw.channels.feishu.handler import FeishuContext
from pyclaw.channels.session_router import SessionRouter
from pyclaw.core.sop_extraction import ExtractionResult
from pyclaw.infra.settings import EvolutionSettings, FeishuSettings
from pyclaw.storage.session.base import InMemorySessionStore

_PATCH_SYNC = "pyclaw.core.sop_extraction.extract_sops_sync"


def _mock_redis() -> MagicMock:
    redis = MagicMock()
    redis.hlen = AsyncMock(return_value=0)
    redis.hgetall = AsyncMock(return_value={})
    redis.set = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    return redis


def _mock_deps() -> MagicMock:
    deps = MagicMock()
    deps.task_manager = MagicMock()
    deps.task_manager.spawn = MagicMock(return_value="t000001")
    deps.llm = MagicMock()
    deps.llm.complete = AsyncMock(return_value=MagicMock(text="[]"))
    deps.session_store = InMemorySessionStore()
    deps.hooks = MagicMock()
    return deps


def _make_ctx(
    *,
    redis_client: Any = None,
    memory_store: Any = None,
    evolution_settings: Any = None,
) -> FeishuContext:
    redis = redis_client if redis_client is not None else _mock_redis()
    feishu_client = MagicMock()
    feishu_client.reply_text = AsyncMock(return_value=None)
    deps = _mock_deps()
    store = deps.session_store
    return FeishuContext(
        settings=FeishuSettings(enabled=True, app_id="cli_x", app_secret="s"),
        feishu_client=feishu_client,
        deps=deps,
        dedup=MagicMock(),
        workspace_store=MagicMock(),
        bot_open_id="bot",
        session_router=SessionRouter(store=store),
        workspace_base=Path("/tmp/test"),
        bootstrap_files=[],
        queue_registry=None,
        redis_client=redis,
        memory_store=(
            memory_store if memory_store is not None else MagicMock()
        ),
        evolution_settings=(
            evolution_settings or EvolutionSettings(enabled=True)
        ),
        nudge_hook=None,
    )


class TestExtractCommandSync:
    @pytest.mark.asyncio
    async def test_extract_writes_sops_replies_with_count(self) -> None:
        ctx = _make_ctx()
        success_result = ExtractionResult(
            spawned=True, llm_returned_count=2, written=2,
        )
        with patch(_PATCH_SYNC, new_callable=AsyncMock, return_value=success_result):
            handled = await handle_command(
                "/extract", "feishu:user", "ses_1", "msg_1",
                MagicMock(), ctx,
            )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "学到 2 条" in reply

    @pytest.mark.asyncio
    async def test_extract_no_candidates_replies(self) -> None:
        ctx = _make_ctx()
        no_cand = ExtractionResult(spawned=False, skip_reason="no_candidates")
        with patch(_PATCH_SYNC, new_callable=AsyncMock, return_value=no_cand):
            handled = await handle_command(
                "/extract", "feishu:user", "ses_1", "msg_1",
                MagicMock(), ctx,
            )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "没有可学习" in reply

    @pytest.mark.asyncio
    async def test_extract_below_threshold_replies(self) -> None:
        ctx = _make_ctx()
        below = ExtractionResult(spawned=False, skip_reason="below_threshold")
        with patch(_PATCH_SYNC, new_callable=AsyncMock, return_value=below):
            await handle_command(
                "/extract", "feishu:user", "ses_1", "msg_1",
                MagicMock(), ctx,
            )
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "工作量不足" in reply

    @pytest.mark.asyncio
    async def test_extract_lock_held_replies(self) -> None:
        ctx = _make_ctx()
        lock = ExtractionResult(spawned=False, skip_reason="lock_held")
        with patch(_PATCH_SYNC, new_callable=AsyncMock, return_value=lock):
            await handle_command(
                "/extract", "feishu:user", "ses_1", "msg_1",
                MagicMock(), ctx,
            )
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "进行中" in reply

    @pytest.mark.asyncio
    async def test_extract_llm_returned_zero_replies(self) -> None:
        ctx = _make_ctx()
        zero = ExtractionResult(spawned=True, llm_returned_count=0)
        with patch(_PATCH_SYNC, new_callable=AsyncMock, return_value=zero):
            await handle_command(
                "/extract", "feishu:user", "ses_1", "msg_1",
                MagicMock(), ctx,
            )
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "不够通用" in reply

    @pytest.mark.asyncio
    async def test_learn_is_alias_for_extract(self) -> None:
        ctx = _make_ctx()
        success = ExtractionResult(spawned=True, llm_returned_count=1, written=1)
        with patch(_PATCH_SYNC, new_callable=AsyncMock, return_value=success):
            handled = await handle_command(
                "/learn", "feishu:user", "ses_1", "msg_1",
                MagicMock(), ctx,
            )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "学到 1 条" in reply

    @pytest.mark.asyncio
    async def test_extract_when_evolution_disabled(self) -> None:
        ctx = _make_ctx()
        ctx.redis_client = None
        ctx.memory_store = None
        handled = await handle_command(
            "/extract", "feishu:user", "ses_1", "msg_1",
            MagicMock(), ctx,
        )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "未启用" in reply

    @pytest.mark.asyncio
    async def test_extract_when_llm_client_none(self) -> None:
        ctx = _make_ctx()
        ctx.deps.llm = None
        handled = await handle_command(
            "/extract", "feishu:user", "ses_1", "msg_1",
            MagicMock(), ctx,
        )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "未启用" in reply

    @pytest.mark.asyncio
    async def test_extract_when_session_store_none(self) -> None:
        ctx = _make_ctx()
        ctx.deps.session_store = None
        handled = await handle_command(
            "/extract", "feishu:user", "ses_1", "msg_1",
            MagicMock(), ctx,
        )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "未启用" in reply

    @pytest.mark.asyncio
    async def test_first_extract_within_cooldown(self) -> None:
        ctx = _make_ctx()
        ctx.redis_client.set = AsyncMock(return_value=True)
        success = ExtractionResult(spawned=True, llm_returned_count=1, written=1)
        with patch(_PATCH_SYNC, new_callable=AsyncMock, return_value=success):
            handled = await handle_command(
                "/extract", "feishu:user", "ses_1", "msg_1",
                MagicMock(), ctx,
            )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "学到 1 条" in reply

    @pytest.mark.asyncio
    async def test_second_extract_cooldown_blocks(self) -> None:
        ctx = _make_ctx()
        ctx.redis_client.set = AsyncMock(return_value=None)
        handled = await handle_command(
            "/extract", "feishu:user", "ses_1", "msg_1",
            MagicMock(), ctx,
        )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "频繁" in reply or "1 分钟" in reply

    @pytest.mark.asyncio
    async def test_ratelimit_redis_failure_fails_open(self) -> None:
        ctx = _make_ctx()
        ctx.redis_client.set = AsyncMock(side_effect=ConnectionError("redis down"))
        success = ExtractionResult(spawned=True, llm_returned_count=1, written=1)
        with patch(_PATCH_SYNC, new_callable=AsyncMock, return_value=success):
            handled = await handle_command(
                "/extract", "feishu:user", "ses_1", "msg_1",
                MagicMock(), ctx,
            )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "频繁" not in reply

    @pytest.mark.asyncio
    async def test_extract_timeout_replies(self) -> None:
        ctx = _make_ctx()
        with patch(
            _PATCH_SYNC,
            new_callable=AsyncMock,
            side_effect=TimeoutError(),
        ):
            handled = await handle_command(
                "/extract", "feishu:user", "ses_1", "msg_1",
                MagicMock(), ctx,
            )
        assert handled is True
        reply = ctx.feishu_client.reply_text.call_args[0][1]
        assert "超时" in reply or "中止" in reply
