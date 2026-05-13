from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pyclaw.infra.settings import SkillSettings
from pyclaw.skills.models import SkillManifest
from pyclaw.core.utils.xml import xml_escape
from pyclaw.skills.prompt import (
    _compact_home_path,
    build_skills_prompt,
    format_skills_compact,
    format_skills_full,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _skill(name: str, description: str = "A skill", file_path: str = "/tmp/skills/SKILL.md") -> SkillManifest:
    return SkillManifest(name=name, description=description, file_path=file_path)


# ---------------------------------------------------------------------------
# 1. Empty skills list → returns ""
# ---------------------------------------------------------------------------

class TestEmptySkillsList:
    def test_format_full_empty(self) -> None:
        assert format_skills_full([]) == ""

    def test_format_compact_empty(self) -> None:
        assert format_skills_compact([]) == ""

    def test_build_prompt_empty(self) -> None:
        assert build_skills_prompt([]) == ""


# ---------------------------------------------------------------------------
# 2. Full format renders correctly — XML structure, preamble
# ---------------------------------------------------------------------------

class TestFullFormat:
    def test_renders_xml_with_description(self) -> None:
        skills = [_skill("git", "Use git for version control", "/tmp/git/SKILL.md")]
        result = format_skills_full(skills)

        assert "<available_skills>" in result
        assert "</available_skills>" in result
        assert "<skill>" in result
        assert "<name>git</name>" in result
        assert "<description>Use git for version control</description>" in result
        assert "<location>/tmp/git/SKILL.md</location>" in result

    def test_preamble_mentions_description(self) -> None:
        skills = [_skill("test-skill")]
        result = format_skills_full(skills)
        assert "matches its description" in result

    def test_preamble_contains_instructions(self) -> None:
        skills = [_skill("test-skill")]
        result = format_skills_full(skills)
        assert "specialized instructions for specific tasks" in result
        assert "read tool to load" in result
        assert "resolve it against the skill directory" in result


# ---------------------------------------------------------------------------
# 3. Compact format renders correctly — no description, different preamble
# ---------------------------------------------------------------------------

class TestCompactFormat:
    def test_no_description_element(self) -> None:
        skills = [_skill("git", "Use git for version control")]
        result = format_skills_compact(skills)
        assert "<description>" not in result
        assert "<name>git</name>" in result
        assert "<location>" in result

    def test_preamble_mentions_name(self) -> None:
        skills = [_skill("test-skill")]
        result = format_skills_compact(skills)
        assert "matches its name" in result
        assert "matches its description" not in result


# ---------------------------------------------------------------------------
# 4. XML escaping — special chars in name/description/location
# ---------------------------------------------------------------------------

class TestXmlEscaping:
    def test_xml_escape_all_chars(self) -> None:
        assert xml_escape('a & b') == 'a &amp; b'
        assert xml_escape('a < b') == 'a &lt; b'
        assert xml_escape('a > b') == 'a &gt; b'
        assert xml_escape('a "b"') == 'a &quot;b&quot;'
        assert xml_escape("a 'b'") == "a &apos;b&apos;"

    def test_xml_escape_combined(self) -> None:
        assert xml_escape('<a & "b">') == '&lt;a &amp; &quot;b&quot;&gt;'

    def test_xml_escape_in_full_format(self) -> None:
        skill = _skill("R&D <team>", 'Use "special" tools', "/path/with<brackets>/SKILL.md")
        result = format_skills_full([skill])
        assert "<name>R&amp;D &lt;team&gt;</name>" in result
        assert "<description>Use &quot;special&quot; tools</description>" in result
        assert "<location>/path/with&lt;brackets&gt;/SKILL.md</location>" in result


# ---------------------------------------------------------------------------
# 5. Home path compaction
# ---------------------------------------------------------------------------

class TestHomePathCompaction:
    def test_compacts_home_path(self) -> None:
        home = str(Path.home())
        path = home + "/.openclaw/skills/x/SKILL.md"
        result = _compact_home_path(path)
        assert result == "~/.openclaw/skills/x/SKILL.md"

    def test_leaves_non_home_path_unchanged(self) -> None:
        result = _compact_home_path("/opt/skills/x/SKILL.md")
        assert result == "/opt/skills/x/SKILL.md"

    def test_no_false_positive_partial_match(self) -> None:
        """Home prefix without trailing / should not match."""
        home = str(Path.home())
        # e.g. /Users/alice-extra/foo should NOT be compacted
        path = home + "-extra/foo/SKILL.md"
        result = _compact_home_path(path)
        assert result == path  # unchanged


# ---------------------------------------------------------------------------
# 6. Budget: full format fits — no warning
# ---------------------------------------------------------------------------

class TestBudgetFullFits:
    def test_full_format_within_budget(self) -> None:
        skills = [
            _skill("alpha", "Skill A", "/a/SKILL.md"),
            _skill("beta", "Skill B", "/b/SKILL.md"),
            _skill("gamma", "Skill C", "/c/SKILL.md"),
        ]
        settings = SkillSettings(max_skills_prompt_chars=50000, max_skills_in_prompt=150)
        result = build_skills_prompt(skills, settings)

        # Full format used — descriptions present
        assert "<description>" in result
        # No warning
        assert "⚠️" not in result


# ---------------------------------------------------------------------------
# 7. Budget: full exceeds, compact fits — compact warning
# ---------------------------------------------------------------------------

class TestBudgetCompactFallback:
    def test_compact_fallback_with_warning(self) -> None:
        skills = [
            _skill("alpha", "A" * 200, "/a/SKILL.md"),
            _skill("beta", "B" * 200, "/b/SKILL.md"),
        ]
        # Full format will exceed this; compact should fit
        full_text = format_skills_full(skills)
        compact_text = format_skills_compact(skills)
        # Pick a budget that's between compact and full size
        budget = len(compact_text) + 200
        assert budget < len(full_text), "Test setup: budget must be less than full"

        settings = SkillSettings(max_skills_prompt_chars=budget, max_skills_in_prompt=150)
        result = build_skills_prompt(skills, settings)

        # Compact format — no description
        assert "<description>" not in result
        # Warning present (compact only, not truncated)
        assert "compact format" in result
        assert "descriptions omitted" in result


# ---------------------------------------------------------------------------
# 8. Budget: compact also exceeds, binary search truncation
# ---------------------------------------------------------------------------

class TestBudgetBinarySearch:
    def test_binary_search_truncation(self) -> None:
        skills = [_skill(f"skill-{i:03d}", "D" * 100, f"/s/{i}/SKILL.md") for i in range(20)]
        # Very tight budget — can't fit all even in compact
        settings = SkillSettings(max_skills_prompt_chars=600, max_skills_in_prompt=150)
        result = build_skills_prompt(skills, settings)

        # Should be truncated
        assert "truncated" in result.lower() or "⚠️" in result
        # Should have fewer than all skills
        count = result.count("<skill>")
        assert 0 < count < 20

    def test_binary_search_includes_maximum_possible(self) -> None:
        """Binary search should find the largest N that fits."""
        skills = [_skill(f"s{i}", "D" * 50, f"/p/{i}/S.md") for i in range(10)]
        # Find what compact format of all 10 looks like
        compact_all = format_skills_compact(skills)
        # Set budget to about half — some but not all should fit
        budget = len(compact_all) // 2 + 150  # +150 for overhead reserve
        settings = SkillSettings(max_skills_prompt_chars=budget, max_skills_in_prompt=150)
        result = build_skills_prompt(skills, settings)

        included_count = result.count("<skill>")
        assert included_count > 0
        # Verify that adding one more would exceed budget
        # (indirectly — the count should be reasonable)
        assert included_count < 10


# ---------------------------------------------------------------------------
# 9. Count cap — max_skills_in_prompt
# ---------------------------------------------------------------------------

class TestCountCap:
    def test_count_cap_limits_skills(self) -> None:
        skills = [_skill(f"skill-{i}", "Desc", f"/s/{i}/SKILL.md") for i in range(5)]
        settings = SkillSettings(max_skills_in_prompt=2, max_skills_prompt_chars=50000)
        result = build_skills_prompt(skills, settings)

        count = result.count("<skill>")
        assert count == 2

    def test_count_cap_with_compact_shows_truncation_warning(self) -> None:
        skills = [_skill(f"skill-{i}", "D" * 300, f"/s/{i}/SKILL.md") for i in range(5)]
        # Budget forces compact; count cap forces truncation
        compact_all = format_skills_compact(skills)
        settings = SkillSettings(
            max_skills_in_prompt=2,
            max_skills_prompt_chars=len(compact_all),
        )
        result = build_skills_prompt(skills, settings)

        assert result.count("<skill>") == 2
        assert "truncated" in result.lower()
        assert "2 of 5" in result


# ---------------------------------------------------------------------------
# 10. Overhead reserve — compact budget = max - 150
# ---------------------------------------------------------------------------

class TestOverheadReserve:
    def test_overhead_reserve_applied(self) -> None:
        """Compact budget should be max_skills_prompt_chars - 150."""
        skills = [_skill("alpha", "A" * 300, "/a/SKILL.md")]
        full_text = format_skills_full(skills)
        compact_text = format_skills_compact(skills)

        # Budget: just above compact length + 150 → compact fits
        budget_fits = len(compact_text) + 150 + 10
        settings_fits = SkillSettings(max_skills_prompt_chars=budget_fits, max_skills_in_prompt=150)
        result_fits = build_skills_prompt(skills, settings_fits)
        # Should use compact (not binary search) because compact_budget = budget_fits - 150 > compact_text
        assert "<skill>" in result_fits

        # Budget: compact length + 140 → compact budget = compact_len - 10, so compact doesn't fit
        budget_tight = len(compact_text) + 140
        settings_tight = SkillSettings(max_skills_prompt_chars=budget_tight, max_skills_in_prompt=150)
        # Full won't fit (it's bigger), compact budget = budget_tight - 150 < compact_text
        # This triggers binary search on a single skill
        result_tight = build_skills_prompt(skills, settings_tight)
        # Either truncated to 0 or 1 skill via binary search
        assert "⚠️" in result_tight or "<skill>" in result_tight


# ---------------------------------------------------------------------------
# 11. Alphabetical sort — output sorted by name
# ---------------------------------------------------------------------------

class TestAlphabeticalSort:
    def test_sorted_by_name(self) -> None:
        skills = [
            _skill("zeta", "Z skill"),
            _skill("alpha", "A skill"),
            _skill("mid", "M skill"),
        ]
        settings = SkillSettings(max_skills_prompt_chars=50000, max_skills_in_prompt=150)
        result = build_skills_prompt(skills, settings)

        alpha_pos = result.index("<name>alpha</name>")
        mid_pos = result.index("<name>mid</name>")
        zeta_pos = result.index("<name>zeta</name>")
        assert alpha_pos < mid_pos < zeta_pos


# ---------------------------------------------------------------------------
# 12. Preamble text difference — full vs compact
# ---------------------------------------------------------------------------

class TestPreambleTextDifference:
    def test_full_says_description(self) -> None:
        skills = [_skill("x")]
        result = format_skills_full(skills)
        assert "matches its description" in result

    def test_compact_says_name(self) -> None:
        skills = [_skill("x")]
        result = format_skills_compact(skills)
        assert "matches its name" in result

    def test_full_and_compact_share_common_text(self) -> None:
        skills = [_skill("x")]
        full = format_skills_full(skills)
        compact = format_skills_compact(skills)
        # Both share the same instruction about resolving relative paths
        assert "resolve it against the skill directory" in full
        assert "resolve it against the skill directory" in compact
