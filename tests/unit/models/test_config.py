from __future__ import annotations

import pytest

from pyclaw.models import (
    AgentRunConfig,
    CompactionConfig,
    CompactResult,
    RetryConfig,
    TimeoutConfig,
    ToolsConfig,
)


def test_agent_run_config_defaults_include_nested_sections() -> None:
    config = AgentRunConfig()
    assert isinstance(config.timeouts, TimeoutConfig)
    assert isinstance(config.retry, RetryConfig)
    assert isinstance(config.compaction, CompactionConfig)
    assert isinstance(config.tools, ToolsConfig)


def test_timeout_config_defaults() -> None:
    cfg = TimeoutConfig()
    assert cfg.run_seconds == 300.0
    assert cfg.idle_seconds == 60.0
    assert cfg.tool_seconds == 120.0
    assert cfg.compaction_seconds == 900.0


def test_retry_config_defaults() -> None:
    cfg = RetryConfig()
    assert cfg.planning_only_limit == 1
    assert cfg.reasoning_only_limit == 2
    assert cfg.empty_response_limit == 1
    assert cfg.unknown_tool_threshold == 3


def test_compaction_config_defaults() -> None:
    cfg = CompactionConfig()
    assert cfg.model is None
    assert cfg.threshold == 0.8
    assert cfg.keep_recent_tokens == 20_000
    assert cfg.timeout_seconds == 900.0
    assert cfg.truncate_after_compaction is False


def test_tools_config_defaults() -> None:
    cfg = ToolsConfig()
    assert cfg.max_output_chars == 25_000


def test_agent_run_config_accepts_nested_overrides() -> None:
    config = AgentRunConfig.model_validate(
        {
            "timeouts": {"run_seconds": 60, "idle_seconds": 0},
            "retry": {"planning_only_limit": 3},
            "compaction": {"model": "openai/gpt-4o-mini"},
            "tools": {"max_output_chars": 5000},
        }
    )
    assert config.timeouts.run_seconds == 60
    assert config.timeouts.idle_seconds == 0
    assert config.retry.planning_only_limit == 3
    assert config.compaction.model == "openai/gpt-4o-mini"
    assert config.tools.max_output_chars == 5000


def test_compact_result_reason_code_field() -> None:
    result = CompactResult(ok=False, compacted=False, reason_code="timeout")
    assert result.reason_code == "timeout"


def test_compact_result_backward_compat_reason_field() -> None:
    result = CompactResult(ok=True, compacted=True, reason="success")
    assert result.reason == "success"
    assert result.reason_code is None


def test_compact_result_rejects_invalid_reason_code() -> None:
    with pytest.raises(ValueError):
        CompactResult(ok=False, compacted=False, reason_code="not_a_real_code")  # type: ignore[arg-type]
