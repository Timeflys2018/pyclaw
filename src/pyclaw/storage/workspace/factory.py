from __future__ import annotations

from pathlib import Path
from typing import Any

from pyclaw.storage.workspace.base import WorkspaceStore
from pyclaw.storage.workspace.file import FileWorkspaceStore


def create_workspace_store(
    settings: Any,
    redis_client: Any = None,
) -> WorkspaceStore:
    backend = settings.workspaces.backend

    if backend == "file":
        return FileWorkspaceStore(Path(settings.workspaces.default).expanduser())

    if backend == "redis":
        if redis_client is None:
            raise ValueError(
                "workspaces.backend='redis' requires redis_client — "
                "ensure Redis is configured and pass redis_client to create_workspace_store()"
            )
        from pyclaw.storage.workspace.redis import RedisWorkspaceStore
        return RedisWorkspaceStore(redis_client, key_prefix=settings.redis.key_prefix)

    raise ValueError(f"unknown workspaces.backend: {backend!r} — valid values: 'file', 'redis'")
