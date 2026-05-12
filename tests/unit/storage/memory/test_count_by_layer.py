"""Tests for SqliteMemoryBackend.count_by_layer (Phase A4a)."""

from __future__ import annotations

import time

import pytest

from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend


@pytest.fixture
def backend(tmp_path):
    return SqliteMemoryBackend(base_dir=tmp_path)


def _entry(eid: str, layer: str, status: str = "active") -> MemoryEntry:
    now = time.time()
    return MemoryEntry(
        id=eid,
        layer=layer,
        type="insight" if layer == "L2" else "procedure",
        content=f"content-{eid}",
        source_session_id="sess",
        created_at=now,
        updated_at=now,
        status=status,
    )


@pytest.mark.asyncio
async def test_count_by_layer_empty(backend: SqliteMemoryBackend) -> None:
    result = await backend.count_by_layer("test:user_x")
    assert result == {"l2": 0, "l3": 0, "l4": 0}


@pytest.mark.asyncio
async def test_count_by_layer_l2_facts(backend: SqliteMemoryBackend) -> None:
    await backend.store("test:user_x", _entry("f1", "L2"))
    await backend.store("test:user_x", _entry("f2", "L2"))
    result = await backend.count_by_layer("test:user_x")
    assert result["l2"] == 2
    assert result["l3"] == 0


@pytest.mark.asyncio
async def test_count_by_layer_l3_active_only(backend: SqliteMemoryBackend) -> None:
    await backend.store("test:user_x", _entry("p1", "L3", status="active"))
    await backend.store("test:user_x", _entry("p2", "L3", status="active"))
    await backend.store("test:user_x", _entry("p3", "L3", status="archived"))
    result = await backend.count_by_layer("test:user_x")
    assert result["l3"] == 2


@pytest.mark.asyncio
async def test_count_by_layer_l4_archives(backend: SqliteMemoryBackend) -> None:
    await backend.archive_session("test:user_x", "session_id_1", "summary 1")
    await backend.archive_session("test:user_x", "session_id_2", "summary 2")
    result = await backend.count_by_layer("test:user_x")
    assert result["l4"] == 2


@pytest.mark.asyncio
async def test_count_by_layer_isolates_by_session_key(backend: SqliteMemoryBackend) -> None:
    await backend.store("test:user_a", _entry("a1", "L2"))
    await backend.store("test:user_b", _entry("b1", "L2"))
    result_a = await backend.count_by_layer("test:user_a")
    result_b = await backend.count_by_layer("test:user_b")
    assert result_a["l2"] == 1
    assert result_b["l2"] == 1
