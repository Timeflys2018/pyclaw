from __future__ import annotations

from pathlib import Path
from typing import Self

from pydantic import BaseModel, Field, model_validator


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
    history_threshold: float = Field(
        default=0.8,
        alias="historyThreshold",
        description="Fraction of history_budget that triggers compaction.",
    )
    threshold: float | None = Field(
        default=None,
        exclude=True,
        description="DEPRECATED: use history_threshold (alias historyThreshold).",
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

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _migrate_threshold(self) -> Self:
        if self.threshold is not None and self.history_threshold == 0.8:
            self.history_threshold = self.threshold
        return self


class ToolsConfig(BaseModel):
    max_output_chars: int = Field(
        default=25_000,
        description="Default cap for tool result content length (per-tool may override).",
    )


class PromptBudgetConfig(BaseModel):
    system_zone_tokens: int = Field(
        default=4096,
        description="Hard cap for frozen zone (identity + tools + skills_index + workspace).",
    )
    dynamic_zone_tokens: int = Field(
        default=4096,
        description="Budget for user message injection area (working_memory, memory_context, etc.).",
    )
    output_reserve_tokens: int | None = Field(
        default=None,
        description="Tokens reserved for LLM output. None = auto-detect from model max output.",
    )
    output_reserve_ratio: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
    )

    def validate_against_context_window(self, max_context_tokens: int) -> None:
        """Validate that fixed zones don't exceed the context window.

        Only validates when output_reserve_tokens is explicitly set.
        When None (auto-detect), validation is deferred to runtime.
        """
        if self.output_reserve_tokens is None:
            return
        total = self.system_zone_tokens + self.dynamic_zone_tokens + self.output_reserve_tokens
        if total > max_context_tokens:
            raise ValueError(
                f"prompt_budget zones ({total}) exceed max_context_tokens ({max_context_tokens}): "
                f"system_zone={self.system_zone_tokens} + dynamic_zone={self.dynamic_zone_tokens} "
                f"+ output_reserve={self.output_reserve_tokens} = {total}"
            )

    def compute_history_budget(
        self,
        max_context_tokens: int,
        model_max_output: int | None = None,
    ) -> int:
        """Compute the history budget from context window and budget config.

        Returns the number of tokens available for conversation history.
        """
        remaining = max_context_tokens - self.system_zone_tokens - self.dynamic_zone_tokens
        if self.output_reserve_tokens is not None:
            output_reserve = self.output_reserve_tokens
        elif model_max_output is not None:
            output_reserve = model_max_output
        else:
            output_reserve = int(remaining * self.output_reserve_ratio)
        return max(0, remaining - output_reserve)


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
    prompt_budget: PromptBudgetConfig = Field(default_factory=PromptBudgetConfig)
