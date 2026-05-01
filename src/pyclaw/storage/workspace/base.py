from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WorkspaceStore(Protocol):
    async def get_file(self, workspace_id: str, filename: str) -> str | None: ...

    async def put_file(self, workspace_id: str, filename: str, content: str) -> None: ...
