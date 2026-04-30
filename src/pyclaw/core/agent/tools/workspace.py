from __future__ import annotations

from pathlib import Path

from pyclaw.models import WorkspaceConfig


class WorkspaceBoundaryError(Exception):
    pass


class WorkspaceResolver:
    def __init__(self, config: WorkspaceConfig) -> None:
        self._config = config

    def resolve(self, workspace_id: str) -> Path:
        return self._config.resolve_path(workspace_id)

    def resolve_within(self, workspace_path: Path, relative: str) -> Path:
        workspace_abs = workspace_path.resolve()
        candidate = (workspace_path / relative).expanduser()
        resolved = candidate.resolve() if candidate.exists() else _lexical_resolve(candidate)
        try:
            resolved.relative_to(workspace_abs)
        except ValueError as exc:
            raise WorkspaceBoundaryError(
                f"path {relative!r} escapes workspace {workspace_abs}"
            ) from exc
        return resolved


def _lexical_resolve(path: Path) -> Path:
    parts: list[str] = []
    for part in path.parts:
        if part == "..":
            if parts and parts[-1] not in ("/",):
                parts.pop()
            else:
                parts.append(part)
        elif part == ".":
            continue
        else:
            parts.append(part)
    return Path(*parts) if parts else Path(".")
