from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pyclaw.infra.settings import SkillSettings
from pyclaw.skills.models import SkillManifest
from pyclaw.skills.prompt import format_skills_index
from pyclaw.skills.provider import DefaultSkillProvider


def _skill(
    name: str,
    description: str = "A skill",
    file_path: str = "/tmp/skills/SKILL.md",
    body: str = "full body text",
) -> SkillManifest:
    return SkillManifest(name=name, description=description, file_path=file_path, body=body)


# ---------------------------------------------------------------------------
# format_skills_index
# ---------------------------------------------------------------------------


class TestFormatSkillsIndex:
    def test_renders_name_and_description_only(self) -> None:
        skills = [_skill("git", "Use git for version control", "/tmp/git/SKILL.md")]
        result = format_skills_index(skills)

        assert "<name>git</name>" in result
        assert "<description>Use git for version control</description>" in result
        assert "<location>" not in result

    def test_xml_escaping(self) -> None:
        skills = [_skill("R&D <team>", "tools & stuff")]
        result = format_skills_index(skills)

        assert "<name>R&amp;D &lt;team&gt;</name>" in result
        assert "<description>tools &amp; stuff</description>" in result

    def test_empty_list(self) -> None:
        assert format_skills_index([]) == ""

    def test_preamble_mentions_skill_view(self) -> None:
        skills = [_skill("test-skill")]
        result = format_skills_index(skills)

        assert "skill_view" in result
        assert "<available_skills>" in result
        assert "</available_skills>" in result


# ---------------------------------------------------------------------------
# resolve_skills_prompt with progressive_disclosure
# ---------------------------------------------------------------------------


class TestResolveSkillsPromptProgressive:
    def _make_eligible(self) -> list[SkillManifest]:
        return [
            _skill("alpha", "Skill A", "/a/SKILL.md", "body A"),
            _skill("beta", "Skill B", "/b/SKILL.md", "body B"),
        ]

    def test_progressive_on_uses_format_skills_index(self, tmp_path: Path) -> None:
        settings = SkillSettings(progressive_disclosure=True)
        provider = DefaultSkillProvider(settings)
        eligible = self._make_eligible()

        with (
            patch("pyclaw.skills.provider.discover_skills", return_value=eligible),
            patch("pyclaw.skills.provider.filter_eligible", return_value=eligible),
        ):
            result = provider.resolve_skills_prompt(str(tmp_path))

        assert result is not None
        assert "<location>" not in result
        assert "skill_view" in result

    def test_progressive_off_uses_build_skills_prompt(self, tmp_path: Path) -> None:
        settings = SkillSettings(progressive_disclosure=False)
        provider = DefaultSkillProvider(settings)
        eligible = self._make_eligible()

        with (
            patch("pyclaw.skills.provider.discover_skills", return_value=eligible),
            patch("pyclaw.skills.provider.filter_eligible", return_value=eligible),
        ):
            result = provider.resolve_skills_prompt(str(tmp_path))

        assert result is not None
        assert "<location>" in result


# ---------------------------------------------------------------------------
# _skills_cache and get_skill_detail
# ---------------------------------------------------------------------------


class TestSkillsCache:
    def _make_eligible(self) -> list[SkillManifest]:
        return [
            _skill("alpha", "Skill A", "/a/SKILL.md", "body A"),
            _skill("beta", "Skill B", "/b/SKILL.md", "body B"),
        ]

    def test_get_skill_detail_after_discovery(self, tmp_path: Path) -> None:
        settings = SkillSettings(progressive_disclosure=True)
        provider = DefaultSkillProvider(settings)
        eligible = self._make_eligible()

        with (
            patch("pyclaw.skills.provider.discover_skills", return_value=eligible),
            patch("pyclaw.skills.provider.filter_eligible", return_value=eligible),
        ):
            provider.resolve_skills_prompt(str(tmp_path))

        assert provider.get_skill_detail("alpha") == "body A"
        assert provider.get_skill_detail("beta") == "body B"

    def test_get_skill_detail_before_discovery(self) -> None:
        settings = SkillSettings()
        provider = DefaultSkillProvider(settings)
        assert provider.get_skill_detail("anything") is None

    def test_get_skill_detail_not_found(self, tmp_path: Path) -> None:
        settings = SkillSettings(progressive_disclosure=True)
        provider = DefaultSkillProvider(settings)
        eligible = self._make_eligible()

        with (
            patch("pyclaw.skills.provider.discover_skills", return_value=eligible),
            patch("pyclaw.skills.provider.filter_eligible", return_value=eligible),
        ):
            provider.resolve_skills_prompt(str(tmp_path))

        assert provider.get_skill_detail("nonexistent") is None
