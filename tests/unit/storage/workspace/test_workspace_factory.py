from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyclaw.infra.settings import Settings
from pyclaw.storage.workspace.factory import create_workspace_store
from pyclaw.storage.workspace.file import FileWorkspaceStore
from pyclaw.storage.workspace.redis import RedisWorkspaceStore


def _settings(backend: str = "file", default: str = "~/.pyclaw/workspace") -> Settings:
    return Settings.model_validate(
        {
            "workspaces": {"backend": backend, "default": default},
            "redis": {"keyPrefix": "pyclaw:"},
        }
    )


def test_factory_returns_file_store_by_default() -> None:
    settings = _settings(backend="file")
    store = create_workspace_store(settings)
    assert isinstance(store, FileWorkspaceStore)


def test_factory_file_store_uses_configured_path(tmp_path: Path) -> None:
    settings = _settings(backend="file", default=str(tmp_path))
    store = create_workspace_store(settings)
    assert isinstance(store, FileWorkspaceStore)
    assert store._base == tmp_path


def test_factory_returns_redis_store_when_configured() -> None:
    settings = _settings(backend="redis")
    mock_client = MagicMock()
    store = create_workspace_store(settings, redis_client=mock_client)
    assert isinstance(store, RedisWorkspaceStore)


def test_factory_raises_when_redis_backend_without_client() -> None:
    settings = _settings(backend="redis")
    with pytest.raises(ValueError, match="requires redis_client"):
        create_workspace_store(settings, redis_client=None)


def test_factory_raises_on_unknown_backend() -> None:
    settings = _settings(backend="s3")
    with pytest.raises(ValueError, match="unknown workspaces.backend"):
        create_workspace_store(settings)
