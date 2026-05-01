from __future__ import annotations

from pathlib import Path


class FileWorkspaceStore:
    def __init__(self, base_dir: Path = Path.home() / ".pyclaw/workspaces") -> None:
        self._base = base_dir

    async def get_file(self, workspace_id: str, filename: str) -> str | None:
        p = self._base / workspace_id / filename
        try:
            return p.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    async def put_file(self, workspace_id: str, filename: str, content: str) -> None:
        p = self._base / workspace_id / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
