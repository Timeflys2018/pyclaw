from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from pyclaw.infra.settings import EmbeddingSettings, MemorySettings, StorageSettings
from pyclaw.storage.memory.base import MemoryStore

if TYPE_CHECKING:
    import redis.asyncio as aioredis


def create_memory_store(
    settings: StorageSettings,
    memory_settings: MemorySettings,
    embedding_settings: EmbeddingSettings,
    redis_client: aioredis.Redis | None = None,
    *,
    key_prefix: str = "pyclaw:",
) -> MemoryStore:
    backend = settings.memory_backend
    if backend == "sqlite":
        if redis_client is None:
            msg = "memory_backend='sqlite' requires a Redis client for L1 index"
            raise ValueError(msg)
        from pyclaw.storage.memory.composite import CompositeMemoryStore
        from pyclaw.storage.memory.embedding import EmbeddingClient
        from pyclaw.storage.memory.redis_index import RedisL1Index
        from pyclaw.storage.memory.sqlite import SqliteMemoryBackend

        base_dir = Path(memory_settings.base_dir).expanduser()
        base_dir.mkdir(parents=True, exist_ok=True)

        embedding = EmbeddingClient(
            model=embedding_settings.model,
            api_key=embedding_settings.api_key,
            api_base=embedding_settings.base_url,
            dimensions=embedding_settings.dimensions,
        )
        return CompositeMemoryStore(
            l1=RedisL1Index(
                redis_client,
                key_prefix=key_prefix,
                max_entries=memory_settings.l1_max_entries,
                max_chars=memory_settings.l1_max_chars,
                ttl_seconds=memory_settings.l1_ttl_seconds,
            ),
            sqlite=SqliteMemoryBackend(
                base_dir,
                embedding,
                fts_min_query_chars=memory_settings.search_fts_min_query_chars,
            ),
        )
    raise ValueError(f"unknown memory_backend: {backend!r}")
