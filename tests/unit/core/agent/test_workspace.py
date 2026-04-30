from __future__ import annotations

from pathlib import Path

import pytest

from pyclaw.core.agent.tools.workspace import WorkspaceBoundaryError, WorkspaceResolver
from pyclaw.models import WorkspaceConfig


def test_resolve_default_returns_configured_path(tmp_path: Path) -> None:
    config = WorkspaceConfig(workspaces={"default": str(tmp_path)})
    resolver = WorkspaceResolver(config)
    assert resolver.resolve("default") == tmp_path.resolve()


def test_resolve_missing_falls_back_to_default(tmp_path: Path) -> None:
    config = WorkspaceConfig(workspaces={"default": str(tmp_path)})
    resolver = WorkspaceResolver(config)
    assert resolver.resolve("not-configured") == tmp_path.resolve()


def test_resolve_within_allows_inside_paths(tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")
    config = WorkspaceConfig(workspaces={"default": str(tmp_path)})
    resolver = WorkspaceResolver(config)
    resolved = resolver.resolve_within(tmp_path.resolve(), "file.txt")
    assert resolved == (tmp_path / "file.txt").resolve()


def test_resolve_within_rejects_traversal(tmp_path: Path) -> None:
    config = WorkspaceConfig(workspaces={"default": str(tmp_path)})
    resolver = WorkspaceResolver(config)
    with pytest.raises(WorkspaceBoundaryError):
        resolver.resolve_within(tmp_path.resolve(), "../etc/passwd")
