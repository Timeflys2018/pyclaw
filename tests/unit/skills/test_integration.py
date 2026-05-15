from __future__ import annotations

import sys
from pathlib import Path
from textwrap import dedent

from pyclaw.skills.discovery import discover_skills
from pyclaw.skills.eligibility import filter_eligible
from pyclaw.skills.prompt import build_skills_prompt

SKILL_TEMPLATE = dedent("""\
    ---
    name: {name}
    description: "{description}"
    {extra_yaml}
    ---
    Body of {name}.
""")


def _write_skill(
    root: Path,
    name: str,
    *,
    description: str = "A test skill",
    extra_yaml: str = "",
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    content = SKILL_TEMPLATE.format(
        name=name,
        description=description,
        extra_yaml=extra_yaml,
    )
    (skill_dir / "SKILL.md").write_text(content)
    return skill_dir


class TestEndToEndPipeline:
    def test_discover_filter_prompt(self, tmp_path: Path) -> None:
        skills_root = tmp_path / "skills"
        skills_root.mkdir()

        _write_skill(skills_root, "github", description="GitHub integration")
        _write_skill(skills_root, "docker", description="Docker management")

        impossible_os = "win32" if sys.platform != "win32" else "linux"
        _write_skill(
            skills_root,
            "platform-only",
            description="Platform-specific skill",
            extra_yaml=dedent(f"""\
                metadata:
                  openclaw:
                    os:
                      - {impossible_os}"""),
        )

        all_skills = discover_skills(tmp_path)
        assert len(all_skills) == 3

        eligible = filter_eligible(all_skills)
        eligible_names = {s.name for s in eligible}
        assert "github" in eligible_names
        assert "docker" in eligible_names
        assert "platform-only" not in eligible_names
        assert len(eligible) == 2

        prompt = build_skills_prompt(eligible)
        assert "<available_skills>" in prompt
        assert "</available_skills>" in prompt
        assert "<skill>" in prompt
        assert "<name>docker</name>" in prompt
        assert "<name>github</name>" in prompt
        assert "platform-only" not in prompt

    def test_empty_workspace_returns_empty(self, tmp_path: Path) -> None:
        all_skills = discover_skills(tmp_path)
        assert all_skills == []

        eligible = filter_eligible(all_skills)
        assert eligible == []

        prompt = build_skills_prompt(eligible)
        assert prompt == ""

    def test_prompt_has_xml_structure(self, tmp_path: Path) -> None:
        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        _write_skill(skills_root, "alpha", description="First skill")
        _write_skill(skills_root, "beta", description="Second skill")

        all_skills = discover_skills(tmp_path)
        eligible = filter_eligible(all_skills)
        prompt = build_skills_prompt(eligible)

        assert prompt.count("<skill>") == 2
        assert prompt.count("</skill>") == 2
        assert prompt.count("<name>") == 2
        assert prompt.count("<description>") == 2
        assert prompt.count("<location>") == 2

    def test_disable_model_invocation_filtered(self, tmp_path: Path) -> None:
        skills_root = tmp_path / "skills"
        skills_root.mkdir()
        _write_skill(skills_root, "visible", description="Visible skill")
        _write_skill(
            skills_root,
            "hidden",
            description="Hidden skill",
            extra_yaml="disable-model-invocation: true",
        )

        all_skills = discover_skills(tmp_path)
        assert len(all_skills) == 2

        eligible = filter_eligible(all_skills)
        assert len(eligible) == 1
        assert eligible[0].name == "visible"
