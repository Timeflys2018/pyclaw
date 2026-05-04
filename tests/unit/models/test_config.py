from __future__ import annotations

import pytest

from pyclaw.models import (
    AgentRunConfig,
    CompactionConfig,
    CompactResult,
    PromptBudgetConfig,
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
    assert cfg.history_threshold == 0.8
    assert cfg.threshold is None
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



def test_prompt_budget_config_defaults() -> None:
    cfg = PromptBudgetConfig()
    assert cfg.system_zone_tokens == 4096
    assert cfg.dynamic_zone_tokens == 4096
    assert cfg.output_reserve_tokens is None
    assert cfg.output_reserve_ratio == 0.3


def test_prompt_budget_config_from_json() -> None:
    cfg = PromptBudgetConfig.model_validate(
        {"system_zone_tokens": 8192, "dynamic_zone_tokens": 2048, "output_reserve_tokens": 128000}
    )
    assert cfg.system_zone_tokens == 8192
    assert cfg.dynamic_zone_tokens == 2048
    assert cfg.output_reserve_tokens == 128000


def test_prompt_budget_compute_history_explicit_output() -> None:
    cfg = PromptBudgetConfig(system_zone_tokens=4096, dynamic_zone_tokens=4096, output_reserve_tokens=128000)
    hb = cfg.compute_history_budget(1_000_000)
    assert hb == 1_000_000 - 4096 - 4096 - 128000


def test_prompt_budget_compute_history_model_max_output() -> None:
    cfg = PromptBudgetConfig(system_zone_tokens=4096, dynamic_zone_tokens=4096)
    hb = cfg.compute_history_budget(200_000, model_max_output=32_000)
    assert hb == 200_000 - 4096 - 4096 - 32_000


def test_prompt_budget_compute_history_fallback_ratio() -> None:
    cfg = PromptBudgetConfig(system_zone_tokens=4096, dynamic_zone_tokens=4096, output_reserve_ratio=0.3)
    hb = cfg.compute_history_budget(200_000)
    remaining = 200_000 - 4096 - 4096
    output_reserve = int(remaining * 0.3)
    assert hb == remaining - output_reserve


def test_prompt_budget_validate_ok() -> None:
    cfg = PromptBudgetConfig(system_zone_tokens=4096, dynamic_zone_tokens=4096, output_reserve_tokens=128000)
    cfg.validate_against_context_window(1_000_000)


def test_prompt_budget_validate_exceeds() -> None:
    cfg = PromptBudgetConfig(system_zone_tokens=500_000, dynamic_zone_tokens=500_000, output_reserve_tokens=128000)
    with pytest.raises(ValueError, match="prompt_budget zones"):
        cfg.validate_against_context_window(1_000_000)


def test_prompt_budget_validate_skips_when_none() -> None:
    cfg = PromptBudgetConfig(system_zone_tokens=999_999, dynamic_zone_tokens=999_999)
    cfg.validate_against_context_window(100)


def test_agent_run_config_has_prompt_budget() -> None:
    config = AgentRunConfig()
    assert isinstance(config.prompt_budget, PromptBudgetConfig)
    assert config.prompt_budget.system_zone_tokens == 4096



def test_compaction_config_history_threshold_alias() -> None:
    cfg = CompactionConfig.model_validate({"historyThreshold": 0.7})
    assert cfg.history_threshold == 0.7


def test_compaction_config_legacy_threshold_migration() -> None:
    cfg = CompactionConfig.model_validate({"threshold": 0.85})
    assert cfg.history_threshold == 0.85
