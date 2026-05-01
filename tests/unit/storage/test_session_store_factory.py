from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from pyclaw.infra.settings import StorageSettings
from pyclaw.storage.session.base import InMemorySessionStore
from pyclaw.storage.session.factory import create_session_store
from pyclaw.storage.session.redis import RedisSessionStore


def _redis_deps():
    client = AsyncMock()
    lock = AsyncMock()
    return client, lock


def test_factory_returns_in_memory_for_memory_backend() -> None:
    settings = StorageSettings(session_backend="memory")
    store = create_session_store(settings)
    assert isinstance(store, InMemorySessionStore)


def test_factory_returns_redis_store_for_redis_backend() -> None:
    settings = StorageSettings(session_backend="redis")
    client, lock = _redis_deps()
    store = create_session_store(settings, client, lock)
    assert isinstance(store, RedisSessionStore)


def test_factory_redis_requires_client() -> None:
    settings = StorageSettings(session_backend="redis")
    with pytest.raises(ValueError, match="redis_client"):
        create_session_store(settings)


def test_factory_raises_for_unknown_backend() -> None:
    settings = StorageSettings(session_backend="postgres")
    with pytest.raises(ValueError, match="unknown session_backend"):
        create_session_store(settings)


def test_factory_threads_ttl_to_redis_store() -> None:
    settings = StorageSettings(session_backend="redis")
    client, lock = _redis_deps()
    store = create_session_store(settings, client, lock, ttl_seconds=999)
    assert isinstance(store, RedisSessionStore)
    assert store._ttl == 999
