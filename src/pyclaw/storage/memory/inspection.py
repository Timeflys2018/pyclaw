"""Memory inspection pure ops shared by CLI and Chat handlers (Phase A3-memory)."""

from __future__ import annotations

from typing import Any, Literal

from pyclaw.storage.memory.base import MemoryEntry


def _layers_for_kind(kind: str) -> list[str] | None:
    if kind == "facts":
        return ["L2"]
    if kind == "procedures":
        return ["L3"]
    if kind == "all":
        return None
    return None


async def list_for_user(
    store: Any,
    session_key: str,
    *,
    kind: Literal["facts", "procedures", "all"] = "all",
    limit: int = 50,
) -> list[MemoryEntry]:
    if store is None:
        raise ValueError("memory_store is None")
    layers = _layers_for_kind(kind)
    results = await store.search(
        session_key,
        "",
        layers=layers,
        limit=limit,
    )
    return list(results)[:limit]


async def search_for_user(
    store: Any,
    session_key: str,
    query: str,
    *,
    limit: int = 10,
) -> list[MemoryEntry]:
    if store is None:
        raise ValueError("memory_store is None")
    results = await store.search(session_key, query, limit=limit)
    return list(results)


async def stats_for_user(store: Any, session_key: str) -> dict[str, int]:
    if store is None:
        raise ValueError("memory_store is None")
    return await store.count_by_layer(session_key)
