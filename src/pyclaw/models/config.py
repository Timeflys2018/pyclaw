from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class WorkspaceConfig(BaseModel):
    workspaces: dict[str, str] = Field(default_factory=lambda: {"default": "."})

    def resolve_path(self, workspace_id: str) -> Path:
        raw = self.workspaces.get(workspace_id) or self.workspaces.get("default", ".")
        return Path(raw).expanduser().resolve()


class AgentRunConfig(BaseModel):
    max_iterations: int = 50
    timeout_seconds: float = 300.0
    tool_timeout_seconds: float = 120.0
    context_window: int = 128_000
    compaction_threshold: float = 0.8
    keep_recent_tokens: int = 20_000
