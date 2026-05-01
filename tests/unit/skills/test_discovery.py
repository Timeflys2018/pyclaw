from __future__ import annotations

import os
from pathlib import Path
from textwrap import dedent

import pytest

from pyclaw.skills.discovery import (
    _is_valid_skill_dir,
    _scan_directory,
    _validate_path_containment,
    discover_skills,
)
from pyclaw.infra.settings import SkillSettings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_SKILL_MD = dedent("""\
    ---
    name: {name}
    description: "{description}"
    ---
    Body of {name}.
""")


def _make_skill(root: Path, name: str, *, description: str = "A test skill") -> Path:
    """Create <root>/<name>/SKILL.md with minimal content."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(MINIMAL_SKILL_MD.format(name=name, description=description))
    return skill_file


def _make_skill_raw(root: Path, name: str, content: str) -> Path:
    """Create <root>/<name>/SKILL.md with raw content."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text(content)
    return skill_file


# ---------------------------------------------------------------------------
# 1. _is_valid_skill_dir
# ---------------------------------------------------------------------------

class TestIsValidSkillDir:
    def test_normal_name(self) -> None:
        assert _is_valid_skill_dir("my-skill") is True

    def test_hidden_directory_rejected(self) -> None:
        assert _is_valid_skill_dir(".hidden") is False

    def test_dot_only_rejected(self) -> None:
        assert _is_valid_skill_dir(".") is False

    def test_node_modules_rejected(self) -> None:
        assert _is_valid_skill_dir("node_modules") is False

    def test_underscore_prefix_allowed(self) -> None:
        assert _is_valid_skill_dir("_internal") is True


# ---------------------------------------------------------------------------
# 2. Single directory with valid skills
# ---------------------------------------------------------------------------

class TestScanDirectory:
    def test_discovers_all_skills(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "alpha")
        _make_skill(tmp_path, "beta")
        _make_skill(tmp_path, "gamma")

        results = _scan_directory(
            tmp_path,
            max_file_bytes=256_000,
            max_candidates=300,
            max_skills_loaded=200,
        )

        names = [s.name for s in results]
        assert names == ["alpha", "beta", "gamma"]

    def test_skips_hidden_directory(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, ".internal")
        _make_skill(tmp_path, "visible")

        results = _scan_directory(
            tmp_path,
            max_file_bytes=256_000,
            max_candidates=300,
            max_skills_loaded=200,
        )

        names = [s.name for s in results]
        assert names == ["visible"]

    def test_skips_node_modules(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "node_modules")
        _make_skill(tmp_path, "real-skill")

        results = _scan_directory(
            tmp_path,
            max_file_bytes=256_000,
            max_candidates=300,
            max_skills_loaded=200,
        )

        names = [s.name for s in results]
        assert names == ["real-skill"]

    def test_skips_oversized_skill_md(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "normal")

        big_dir = tmp_path / "toobig"
        big_dir.mkdir()
        big_file = big_dir / "SKILL.md"
        big_file.write_text("---\nname: toobig\n---\n" + "x" * 1000)

        results = _scan_directory(
            tmp_path,
            max_file_bytes=100,
            max_candidates=300,
            max_skills_loaded=200,
        )

        names = [s.name for s in results]
        assert "toobig" not in names
        assert "normal" in names

    def test_per_dir_candidate_cap(self, tmp_path: Path) -> None:
        """Only loads up to max_candidates subdirectories."""
        for i in range(10):
            _make_skill(tmp_path, f"skill-{i:03d}")

        results = _scan_directory(
            tmp_path,
            max_file_bytes=256_000,
            max_candidates=5,
            max_skills_loaded=200,
        )

        assert len(results) == 5

    def test_max_skills_loaded_cap(self, tmp_path: Path) -> None:
        """Stops loading once max_skills_loaded is reached."""
        for i in range(10):
            _make_skill(tmp_path, f"skill-{i:03d}")

        results = _scan_directory(
            tmp_path,
            max_file_bytes=256_000,
            max_candidates=300,
            max_skills_loaded=3,
        )

        assert len(results) == 3

    def test_skips_dir_without_skill_md(self, tmp_path: Path) -> None:
        """Subdirectory without SKILL.md is silently skipped."""
        _make_skill(tmp_path, "has-skill")
        (tmp_path / "no-skill").mkdir()

        results = _scan_directory(
            tmp_path,
            max_file_bytes=256_000,
            max_candidates=300,
            max_skills_loaded=200,
        )

        names = [s.name for s in results]
        assert names == ["has-skill"]

    def test_skips_invalid_yaml(self, tmp_path: Path) -> None:
        """Invalid SKILL.md is skipped with a warning, not a crash."""
        _make_skill(tmp_path, "good")
        _make_skill_raw(tmp_path, "bad", "---\nname: [broken\n---\n")

        results = _scan_directory(
            tmp_path,
            max_file_bytes=256_000,
            max_candidates=300,
            max_skills_loaded=200,
        )

        names = [s.name for s in results]
        assert "good" in names
        assert "bad" not in names

    def test_sorted_alphabetically(self, tmp_path: Path) -> None:
        _make_skill(tmp_path, "zebra")
        _make_skill(tmp_path, "alpha")
        _make_skill(tmp_path, "mid")

        results = _scan_directory(
            tmp_path,
            max_file_bytes=256_000,
            max_candidates=300,
            max_skills_loaded=200,
        )

        names = [s.name for s in results]
        assert names == ["alpha", "mid", "zebra"]


# ---------------------------------------------------------------------------
# 3. _validate_path_containment
# ---------------------------------------------------------------------------

class TestValidatePathContainment:
    def test_valid_path(self, tmp_path: Path) -> None:
        child = tmp_path / "skills" / "my-skill" / "SKILL.md"
        child.parent.mkdir(parents=True)
        child.touch()
        assert _validate_path_containment(child, tmp_path) is True

    def test_escape_rejected(self, tmp_path: Path) -> None:
        """Path that escapes the root is rejected."""
        outside = tmp_path.parent / "outside"
        outside.mkdir(exist_ok=True)
        assert _validate_path_containment(outside, tmp_path) is False

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        """Symlink pointing outside root is rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        outside_skill = outside / "SKILL.md"
        outside_skill.write_text("---\nname: escape\n---\n")

        root = tmp_path / "root"
        root.mkdir()
        link = root / "escape-link"
        try:
            os.symlink(outside, link)
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")

        assert _validate_path_containment(link / "SKILL.md", root) is False


# ---------------------------------------------------------------------------
# 4. discover_skills — integration
# ---------------------------------------------------------------------------

class TestDiscoverSkills:
    def test_no_directories_exist(self, tmp_path: Path) -> None:
        """No crash when directories don't exist."""
        settings = SkillSettings(
            workspace_skills_dir="skills",
            project_agents_skills_dir=".agents/skills",
            managed_skills_dir=str(tmp_path / "nonexistent" / "managed"),
            personal_agents_skills_dir=str(tmp_path / "nonexistent" / "personal"),
            bundled_skills_dir=None,
        )
        result = discover_skills(tmp_path, settings=settings)
        assert result == []

    def test_single_directory(self, tmp_path: Path) -> None:
        """Discovers skills from one directory."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        skills_dir = ws / "skills"
        skills_dir.mkdir()
        _make_skill(skills_dir, "my-skill")

        settings = SkillSettings(
            workspace_skills_dir="skills",
            project_agents_skills_dir=".agents/skills",
            managed_skills_dir=str(tmp_path / "nonexistent" / "managed"),
            personal_agents_skills_dir=str(tmp_path / "nonexistent" / "personal"),
            bundled_skills_dir=None,
        )
        result = discover_skills(ws, settings=settings)

        assert len(result) == 1
        assert result[0].name == "my-skill"

    def test_dedup_higher_priority_wins(self, tmp_path: Path) -> None:
        """Workspace (highest priority) overwrites managed (lower priority)."""
        ws = tmp_path / "workspace"
        ws.mkdir()

        managed = tmp_path / "managed"
        managed.mkdir()
        _make_skill(managed, "shared", description="from managed")

        ws_skills = ws / "skills"
        ws_skills.mkdir()
        _make_skill(ws_skills, "shared", description="from workspace")

        settings = SkillSettings(
            workspace_skills_dir="skills",
            project_agents_skills_dir=".agents/skills",
            managed_skills_dir=str(managed),
            personal_agents_skills_dir=str(tmp_path / "nonexistent"),
            bundled_skills_dir=None,
        )
        result = discover_skills(ws, settings=settings)

        assert len(result) == 1
        assert result[0].name == "shared"
        assert result[0].description == "from workspace"

    def test_five_layer_merge(self, tmp_path: Path) -> None:
        """All 5 layers merge correctly. Higher priority wins for duplicates."""
        ws = tmp_path / "workspace"
        ws.mkdir()

        bundled = tmp_path / "bundled"
        bundled.mkdir()
        _make_skill(bundled, "common", description="from bundled")
        _make_skill(bundled, "bundled-only", description="bundled exclusive")

        personal = tmp_path / "personal"
        personal.mkdir()
        _make_skill(personal, "common", description="from personal")
        _make_skill(personal, "personal-only", description="personal exclusive")

        managed = tmp_path / "managed"
        managed.mkdir()
        _make_skill(managed, "common", description="from managed")

        project_agents = ws / ".agents" / "skills"
        project_agents.mkdir(parents=True)
        _make_skill(project_agents, "common", description="from project-agents")

        ws_skills = ws / "skills"
        ws_skills.mkdir()
        _make_skill(ws_skills, "common", description="from workspace")

        settings = SkillSettings(
            workspace_skills_dir="skills",
            project_agents_skills_dir=".agents/skills",
            managed_skills_dir=str(managed),
            personal_agents_skills_dir=str(personal),
            bundled_skills_dir=str(bundled),
        )
        result = discover_skills(ws, settings=settings)

        by_name = {s.name: s for s in result}
        assert by_name["common"].description == "from workspace"
        assert by_name["bundled-only"].description == "bundled exclusive"
        assert by_name["personal-only"].description == "personal exclusive"

    def test_sorted_output(self, tmp_path: Path) -> None:
        """Final output is alphabetically sorted."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        skills_dir = ws / "skills"
        skills_dir.mkdir()
        _make_skill(skills_dir, "zebra")
        _make_skill(skills_dir, "alpha")
        _make_skill(skills_dir, "mid")

        settings = SkillSettings(
            workspace_skills_dir="skills",
            project_agents_skills_dir=".agents/skills",
            managed_skills_dir=str(tmp_path / "nonexistent"),
            personal_agents_skills_dir=str(tmp_path / "nonexistent"),
            bundled_skills_dir=None,
        )
        result = discover_skills(ws, settings=settings)

        names = [s.name for s in result]
        assert names == ["alpha", "mid", "zebra"]

    def test_default_settings_used(self, tmp_path: Path) -> None:
        """When settings is None, defaults are used without crashing."""
        result = discover_skills(tmp_path, settings=None)
        assert isinstance(result, list)

    def test_symlink_escape_in_scan(self, tmp_path: Path) -> None:
        """Symlinked skill escaping root is rejected during scan."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        skills_dir = ws / "skills"
        skills_dir.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        _make_skill(outside, "escape")

        _make_skill(skills_dir, "safe")

        try:
            os.symlink(outside / "escape", skills_dir / "escape")
        except OSError:
            pytest.skip("Cannot create symlinks on this platform")

        settings = SkillSettings(
            workspace_skills_dir="skills",
            project_agents_skills_dir=".agents/skills",
            managed_skills_dir=str(tmp_path / "nonexistent"),
            personal_agents_skills_dir=str(tmp_path / "nonexistent"),
            bundled_skills_dir=None,
        )
        result = discover_skills(ws, settings=settings)

        names = [s.name for s in result]
        assert "safe" in names
        assert "escape" not in names
