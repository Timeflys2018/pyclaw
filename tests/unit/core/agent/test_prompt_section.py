from __future__ import annotations

from pyclaw.core.agent.system_prompt import PromptSection, SystemPromptResult


class TestPromptSection:
    def test_token_estimation_from_text(self) -> None:
        text = "a" * 400
        section = PromptSection(name="test", text=text)
        assert section.estimated_tokens == 100

    def test_truncatable_default_is_true(self) -> None:
        section = PromptSection(name="test", text="hello")
        assert section.truncatable is True

    def test_truncatable_can_be_false(self) -> None:
        section = PromptSection(name="identity", text="hello", truncatable=False)
        assert section.truncatable is False

    def test_estimated_tokens_uses_integer_division(self) -> None:
        text = "a" * 7
        section = PromptSection(name="short", text=text)
        assert section.estimated_tokens == 1


class TestSystemPromptResult:
    def test_from_sections_joins_text(self) -> None:
        sections = [
            PromptSection(name="a", text="hello"),
            PromptSection(name="b", text="world"),
        ]
        result = SystemPromptResult.from_sections(sections)
        assert result.text == "hello\n\nworld"

    def test_from_sections_builds_token_breakdown(self) -> None:
        sections = [
            PromptSection(name="identity", text="a" * 400),
            PromptSection(name="tools", text="b" * 200),
        ]
        result = SystemPromptResult.from_sections(sections)
        assert result.token_breakdown == {"identity": 100, "tools": 50}

    def test_from_sections_token_breakdown_sum_matches(self) -> None:
        sections = [
            PromptSection(name="a", text="x" * 120),
            PromptSection(name="b", text="y" * 80),
            PromptSection(name="c", text="z" * 40),
        ]
        result = SystemPromptResult.from_sections(sections)
        total = sum(result.token_breakdown.values())
        section_total = sum(s.estimated_tokens for s in result.sections)
        assert total == section_total

    def test_from_sections_empty(self) -> None:
        result = SystemPromptResult.from_sections([])
        assert result.text == ""
        assert result.sections == []
        assert result.token_breakdown == {}

    def test_from_sections_preserves_sections(self) -> None:
        sections = [
            PromptSection(name="identity", text="I am an AI", truncatable=False),
        ]
        result = SystemPromptResult.from_sections(sections)
        assert len(result.sections) == 1
        assert result.sections[0].name == "identity"
        assert result.sections[0].truncatable is False
