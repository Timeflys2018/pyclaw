from __future__ import annotations

from typing import Protocol, runtime_checkable

from pyclaw.storage.session.base import SessionStore

__all__ = ["ConfigStore", "LockManager", "MemoryStore", "SessionStore"]


@runtime_checkable
class MemoryStore(Protocol):
    async def add(self, agent_id: str, entry: dict) -> str: ...
    async def search(self, agent_id: str, query: str, limit: int = 10) -> list[dict]: ...
    async def delete(self, entry_id: str) -> None: ...


@runtime_checkable
class LockManager(Protocol):
    async def acquire(self, key: str, ttl_ms: int = 30_000) -> str: ...
    async def release(self, key: str, token: str) -> bool: ...
    async def renew(self, key: str, token: str, ttl_ms: int = 30_000) -> bool: ...


@runtime_checkable
class ConfigStore(Protocol):
    async def get(self, key: str) -> dict | None: ...
    async def set(self, key: str, value: dict) -> None: ...
