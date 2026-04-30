from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class WorkspaceConfig(BaseModel):
    workspaces: dict[str, str] = Field(default_factory=lambda: {"default": "."})

    def resolve_path(self, workspace_id: str) -> Path:
        raw = self.workspaces.get(workspace_id) or self.workspaces.get("default", ".")
        return Path(raw).expanduser().resolve()


class TimeoutConfig(BaseModel):
    run_seconds: float = Field(
        default=300.0,
        description="Outermost timeout protecting a whole agent turn. 0 disables.",
    )
    idle_seconds: float = Field(
        default=60.0,
        description="Max time with no LLM stream progress before aborting. 0 disables.",
    )
    tool_seconds: float = Field(
        default=120.0,
        description="Default per-tool execution cap when tool does not declare its own. 0 disables.",
    )
    compaction_seconds: float = Field(
        default=900.0,
        description="Safety timeout for a compaction attempt.",
    )


class RetryConfig(BaseModel):
    planning_only_limit: int = Field(
        default=1,
        description="Max retries when assistant returns plan-only text with no tool calls. 0 disables.",
    )
    reasoning_only_limit: int = Field(
        default=2,
        description="Max retries when assistant returns only reasoning content. 0 disables.",
    )
    empty_response_limit: int = Field(
        default=1,
        description="Max retries when assistant returns empty content. 0 disables.",
    )
    unknown_tool_threshold: int = Field(
        default=3,
        description="Consecutive unknown-tool calls before forced termination. 0 disables.",
    )


class CompactionConfig(BaseModel):
    model: str | None = Field(
        default=None,
        description="Override LLM model for summarization. If None, chat model is used.",
    )
    threshold: float = Field(
        default=0.8,
        description="Fraction of context_window that triggers compaction.",
    )
    keep_recent_tokens: int = Field(
        default=20_000,
        description="Minimum tokens of recent messages to preserve uncompacted.",
    )
    timeout_seconds: float = Field(
        default=900.0,
        description="Safety timeout for a compaction attempt (mirrors timeouts.compaction_seconds).",
    )
    truncate_after_compaction: bool = Field(
        default=False,
        description="If True, hard-truncate residual messages after compaction if still over budget.",
    )


class ToolsConfig(BaseModel):
    max_output_chars: int = Field(
        default=25_000,
        description="Default cap for tool result content length (per-tool may override).",
    )


class AgentRunConfig(BaseModel):
    max_iterations: int = 50
    timeout_seconds: float = Field(
        default=300.0,
        description="DEPRECATED: use timeouts.run_seconds.",
    )
    tool_timeout_seconds: float = Field(
        default=120.0,
        description="DEPRECATED: use timeouts.tool_seconds.",
    )
    context_window: int = 128_000
    compaction_threshold: float = Field(
        default=0.8,
        description="DEPRECATED: use compaction.threshold.",
    )
    keep_recent_tokens: int = Field(
        default=20_000,
        description="DEPRECATED: use compaction.keep_recent_tokens.",
    )

    timeouts: TimeoutConfig = Field(default_factory=TimeoutConfig)
    retry: RetryConfig = Field(default_factory=RetryConfig)
    compaction: CompactionConfig = Field(default_factory=CompactionConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
