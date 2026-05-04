from __future__ import annotations

import logging

from pyclaw.core.agent.system_prompt import PromptSection, enforce_system_budget


def _section(name: str, chars: int, truncatable: bool = True) -> PromptSection:
    return PromptSection(name=name, text="x" * chars, truncatable=truncatable)


def test_within_budget_returns_unchanged() -> None:
    sections = [_section("identity", 120, False), _section("tools", 400, False)]
    result = enforce_system_budget(sections, budget=4096)
    assert len(result) == 2
    assert result[0].name == "identity"
    assert result[1].name == "tools"


def test_skills_truncated_first() -> None:
    sections = [
        _section("identity", 120, False),
        _section("tools", 1600, False),
        _section("skills", 6000, True),
        _section("workspace", 120, True),
    ]
    result = enforce_system_budget(sections, budget=1000)
    names = [s.name for s in result]
    assert "identity" in names
    assert "tools" in names
    assert "workspace" in names
    total = sum(s.estimated_tokens for s in result)
    assert total <= 1000 or all(not s.truncatable for s in result if s.estimated_tokens > 0)


def test_multiple_sections_truncated() -> None:
    sections = [
        _section("identity", 40, False),
        _section("tools", 40, False),
        _section("skills", 8000, True),
        _section("bootstrap", 8000, True),
        _section("workspace", 120, True),
    ]
    result = enforce_system_budget(sections, budget=100)
    assert any(s.name == "identity" for s in result)
    assert any(s.name == "tools" for s in result)


def test_identity_and_tools_never_truncated() -> None:
    sections = [
        _section("identity", 8000, False),
        _section("tools", 12000, False),
    ]
    result = enforce_system_budget(sections, budget=100)
    assert len(result) == 2
    assert result[0].estimated_tokens == 2000
    assert result[1].estimated_tokens == 3000


def test_warning_emitted_on_truncation(caplog: logging.LogRecord) -> None:
    sections = [
        _section("identity", 40, False),
        _section("skills", 8000, True),
    ]
    with caplog.at_level(logging.WARNING):
        enforce_system_budget(sections, budget=100)
    assert any("system_zone over budget" in r.message for r in caplog.records)


def test_empty_sections_removed() -> None:
    sections = [
        _section("identity", 40, False),
        _section("skills", 400, True),
    ]
    result = enforce_system_budget(sections, budget=10)
    names = [s.name for s in result]
    assert "skills" not in names
    assert "identity" in names


def test_budget_applied_in_build_frozen_prefix() -> None:
    from pyclaw.core.agent.system_prompt import PromptInputs, build_frozen_prefix

    inputs = PromptInputs(
        session_id="s1",
        workspace_id="default",
        agent_id="main",
        model="gpt-4o",
        skills_prompt="x" * 20000,
        workspace_path="/tmp",
    )
    result = build_frozen_prefix(inputs, budget=100)
    assert result.token_breakdown.get("skills", 0) < 5000
