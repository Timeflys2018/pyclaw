from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pyclaw.infra.settings import EmbeddingSettings, MemorySettings, StorageSettings


def _make_settings(
    backend: str = "sqlite",
) -> tuple[StorageSettings, MemorySettings, EmbeddingSettings]:
    storage = StorageSettings(memory_backend=backend)
    memory = MemorySettings(base_dir="/tmp/pyclaw-test-memory")
    embedding = EmbeddingSettings(
        model="test-model", api_key="test-key", base_url="http://localhost", dimensions=128
    )
    return storage, memory, embedding


def test_unknown_backend_raises_value_error() -> None:
    from pyclaw.storage.memory.factory import create_memory_store

    storage, memory, embedding = _make_settings(backend="postgres")
    with pytest.raises(ValueError, match="unknown memory_backend.*'postgres'"):
        create_memory_store(storage, memory, embedding)


def test_none_redis_client_raises_value_error() -> None:
    from pyclaw.storage.memory.factory import create_memory_store

    storage, memory, embedding = _make_settings()
    with pytest.raises(ValueError, match="requires a Redis client"):
        create_memory_store(storage, memory, embedding, redis_client=None)


def test_sqlite_backend_creates_composite() -> None:
    mock_composite_cls = MagicMock()
    mock_embedding_cls = MagicMock()
    mock_redis_l1_cls = MagicMock()
    mock_sqlite_cls = MagicMock()

    with (
        patch(
            "pyclaw.storage.memory.composite.CompositeMemoryStore",
            mock_composite_cls,
        ),
        patch(
            "pyclaw.storage.memory.embedding.EmbeddingClient",
            mock_embedding_cls,
        ),
        patch(
            "pyclaw.storage.memory.redis_index.RedisL1Index",
            mock_redis_l1_cls,
        ),
        patch(
            "pyclaw.storage.memory.sqlite.SqliteMemoryBackend",
            mock_sqlite_cls,
        ),
    ):
        from importlib import reload

        import pyclaw.storage.memory.factory as factory_mod

        reload(factory_mod)
        from pyclaw.storage.memory.factory import create_memory_store

        storage, memory, embedding = _make_settings()
        redis_client = MagicMock()

        result = create_memory_store(
            storage, memory, embedding, redis_client, key_prefix="test:"
        )

        mock_embedding_cls.assert_called_once_with(
            model="test-model",
            api_key="test-key",
            api_base="http://localhost",
            dimensions=128,
        )
        mock_redis_l1_cls.assert_called_once_with(
            redis_client,
            key_prefix="test:",
            max_entries=memory.l1_max_entries,
            max_chars=memory.l1_max_chars,
            ttl_seconds=memory.l1_ttl_seconds,
        )
        mock_sqlite_cls.assert_called_once()
        mock_composite_cls.assert_called_once_with(
            l1=mock_redis_l1_cls.return_value,
            sqlite=mock_sqlite_cls.return_value,
        )
        assert result == mock_composite_cls.return_value
