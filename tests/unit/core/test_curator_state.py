"""Tests for CuratorStateStore (Phase A2).

Audit-trail anchors: A2.1, A2.3 map to tasks.md.

CuratorStateStore is the sole writer of curator cross-instance Redis keys.
Legacy constants (``CURATOR_LAST_RUN_KEY`` / ``CURATOR_LLM_REVIEW_KEY``) are
kept as deprecated re-exports; the canonical write path is this class.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.core.curator_state import CuratorStateStore


class TestCuratorStateStoreWrites:
    """A2.1: ``mark_*`` methods issue a single Redis SET with correct key + value."""

    @pytest.mark.asyncio
    async def test_mark_scan_completed_writes_float_timestamp(self) -> None:
        redis = AsyncMock()
        store = CuratorStateStore(redis)

        await store.mark_scan_completed()

        redis.set.assert_awaited_once()
        args = redis.set.await_args.args
        assert args[0] == "pyclaw:curator:last_run_at"
        float(args[1])

    @pytest.mark.asyncio
    async def test_mark_review_fully_completed_writes_int_timestamp(self) -> None:
        redis = AsyncMock()
        store = CuratorStateStore(redis)

        await store.mark_review_fully_completed()

        redis.set.assert_awaited_once()
        args = redis.set.await_args.args
        assert args[0] == "pyclaw:curator:llm_review_last_run_at"
        value = args[1]
        assert isinstance(value, str)
        parsed = int(value)
        assert parsed > 0


class TestCuratorStateStoreReads:
    """A2.1: ``get_last_*`` methods parse Redis values, returning None on missing/malformed."""

    @pytest.mark.asyncio
    async def test_get_last_scan_at_returns_none_when_missing(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        store = CuratorStateStore(redis)

        result = await store.get_last_scan_at()

        assert result is None
        redis.get.assert_awaited_once_with("pyclaw:curator:last_run_at")

    @pytest.mark.asyncio
    async def test_get_last_scan_at_parses_float_string(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b"1700000000.5")
        store = CuratorStateStore(redis)

        result = await store.get_last_scan_at()

        assert result == 1700000000.5

    @pytest.mark.asyncio
    async def test_get_last_scan_at_returns_none_on_non_numeric(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b"not-a-number")
        store = CuratorStateStore(redis)

        result = await store.get_last_scan_at()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_last_review_at_returns_none_when_missing(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        store = CuratorStateStore(redis)

        result = await store.get_last_review_at()

        assert result is None
        redis.get.assert_awaited_once_with("pyclaw:curator:llm_review_last_run_at")

    @pytest.mark.asyncio
    async def test_get_last_review_at_parses_int_string(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b"1700000000")
        store = CuratorStateStore(redis)

        result = await store.get_last_review_at()

        assert result == 1700000000

    @pytest.mark.asyncio
    async def test_get_last_review_at_returns_none_on_non_numeric(self) -> None:
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b"garbage")
        store = CuratorStateStore(redis)

        result = await store.get_last_review_at()

        assert result is None

    @pytest.mark.asyncio
    async def test_get_last_review_at_accepts_str_value(self) -> None:
        """Redis clients may return str when decode_responses=True is set."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value="1700000000")
        store = CuratorStateStore(redis)

        result = await store.get_last_review_at()

        assert result == 1700000000


class TestCuratorStateStoreSeed:
    """A2.1: ``seed_if_missing`` uses NX semantics — writes only if absent."""

    @pytest.mark.asyncio
    async def test_seed_if_missing_writes_when_absent(self) -> None:
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=True)
        store = CuratorStateStore(redis)

        await store.seed_if_missing()

        assert redis.set.await_count == 1
        call_kwargs = redis.set.await_args.kwargs
        assert call_kwargs.get("nx") is True

    @pytest.mark.asyncio
    async def test_seed_if_missing_noop_when_present(self) -> None:
        """NX=True makes Redis reject the write when the key exists; the method
        MUST NOT raise nor retry."""
        redis = AsyncMock()
        redis.set = AsyncMock(return_value=None)
        store = CuratorStateStore(redis)

        await store.seed_if_missing()

        assert redis.set.await_count == 1


class TestCuratorStateStoreAPI:
    """A2.3: single-writer invariant is structural (private class attrs)."""

    def test_key_constants_are_private_class_attributes(self) -> None:
        """Legacy top-level constants remain re-exportable; but this class
        owns the canonical value internally so external modules cannot write
        via these specific attribute paths."""
        assert CuratorStateStore._LAST_RUN_KEY == "pyclaw:curator:last_run_at"
        assert CuratorStateStore._LLM_REVIEW_KEY == "pyclaw:curator:llm_review_last_run_at"
