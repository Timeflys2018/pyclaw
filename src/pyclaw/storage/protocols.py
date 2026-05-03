from __future__ import annotations

from typing import Protocol, runtime_checkable

from pyclaw.storage.memory.base import MemoryStore
from pyclaw.storage.session.base import SessionStore

__all__ = ["LockManager", "MemoryStore", "SessionStore"]


@runtime_checkable
class LockManager(Protocol):
    async def acquire(self, key: str, ttl_ms: int = 30_000) -> str: ...
    async def release(self, key: str, token: str) -> bool: ...
    async def renew(self, key: str, token: str, ttl_ms: int = 30_000) -> bool: ...
