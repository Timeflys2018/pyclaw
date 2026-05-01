from __future__ import annotations

import pytest

from pyclaw.infra.settings import Settings, WorkspaceSettings


def test_workspace_settings_default_backend() -> None:
    s = WorkspaceSettings()
    assert s.backend == "file"


def test_workspace_settings_backend_alias() -> None:
    s = Settings.model_validate({"workspaces": {"backend": "redis"}})
    assert s.workspaces.backend == "redis"


def test_workspace_settings_bootstrap_files_default() -> None:
    s = WorkspaceSettings()
    assert s.bootstrap_files == ["AGENTS.md"]


def test_workspace_settings_bootstrap_files_alias() -> None:
    s = Settings.model_validate({"workspaces": {"bootstrapFiles": ["AGENTS.md", "SOUL.md"]}})
    assert s.workspaces.bootstrap_files == ["AGENTS.md", "SOUL.md"]


def test_workspace_settings_existing_config_parses() -> None:
    s = Settings.model_validate({"workspaces": {"default": "~/.custom/workspace"}})
    assert s.workspaces.default == "~/.custom/workspace"
    assert s.workspaces.backend == "file"
    assert s.workspaces.bootstrap_files == ["AGENTS.md"]
