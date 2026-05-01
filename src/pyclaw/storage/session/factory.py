from __future__ import annotations

from typing import Any

from pyclaw.infra.settings import StorageSettings
from pyclaw.storage.protocols import LockManager, SessionStore
from pyclaw.storage.session.base import InMemorySessionStore


def create_session_store(
    settings: StorageSettings,
    redis_client: Any | None = None,
    lock_manager: LockManager | None = None,
    *,
    ttl_seconds: int = 604_800,
) -> SessionStore:
    backend = settings.session_backend

    if backend == "memory":
        return InMemorySessionStore()

    if backend == "redis":
        if redis_client is None or lock_manager is None:
            raise ValueError(
                "redis_client and lock_manager are required for session_backend='redis'"
            )
        from pyclaw.storage.session.redis import RedisSessionStore

        return RedisSessionStore(
            redis_client,
            lock_manager,
            ttl_seconds=ttl_seconds,
        )

    raise ValueError(f"unknown session_backend: {backend!r}")
