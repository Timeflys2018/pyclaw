from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from pyclaw.skills.models import SkillParseError
from pyclaw.skills.parser import parse_skill_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_skill(tmp_path: Path, content: str, *, name: str = "SKILL.md") -> Path:
    """Write a SKILL.md file under tmp_path/<name-dir>/SKILL.md."""
    skill_dir = tmp_path / "my-skill"
    skill_dir.mkdir(exist_ok=True)
    p = skill_dir / name
    p.write_text(dedent(content))
    return p


# ---------------------------------------------------------------------------
# 1. Valid full SKILL.md
# ---------------------------------------------------------------------------

def test_parse_full_skill(tmp_path: Path) -> None:
    """Full SKILL.md with name, description, bins, install specs, emoji, body."""
    p = _write_skill(tmp_path, """\
        ---
        name: github
        description: "Use gh for GitHub issues, PR status, CI/logs."
        metadata:
          openclaw:
            emoji: "🐙"
            requires:
              bins:
                - gh
            install:
              - id: brew
                kind: brew
                formula: gh
                bins:
                  - gh
                label: "Install GitHub CLI (brew)"
        ---

        # GitHub Skill
        Use the `gh` CLI to interact with GitHub repositories.
    """)

    manifest = parse_skill_file(p)

    assert manifest.name == "github"
    assert manifest.description == "Use gh for GitHub issues, PR status, CI/logs."
    assert manifest.emoji == "🐙"
    assert manifest.requirements.bins == ["gh"]
    assert len(manifest.install_specs) == 1
    spec = manifest.install_specs[0]
    assert spec.kind == "brew"
    assert spec.formula == "gh"
    assert spec.bins == ["gh"]
    assert spec.label == "Install GitHub CLI (brew)"
    assert "GitHub Skill" in manifest.body
    assert manifest.file_path == str(p.resolve())


# ---------------------------------------------------------------------------
# 2. Minimal SKILL.md (only name, no metadata)
# ---------------------------------------------------------------------------

def test_parse_minimal_skill(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        name: simple
        ---
    """)

    manifest = parse_skill_file(p)

    assert manifest.name == "simple"
    assert manifest.description == ""
    assert manifest.body == ""
    assert manifest.requirements.bins == []
    assert manifest.install_specs == []
    assert manifest.emoji is None
    assert manifest.always is False


# ---------------------------------------------------------------------------
# 3. Missing name → fallback to directory name
# ---------------------------------------------------------------------------

def test_missing_name_fallback_to_directory(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        description: "A skill without a name."
        ---

        Body text here.
    """)

    manifest = parse_skill_file(p)

    # Parent dir is "my-skill" (set by _write_skill helper)
    assert manifest.name == "my-skill"
    assert manifest.description == "A skill without a name."


# ---------------------------------------------------------------------------
# 4. Missing description → defaults to ""
# ---------------------------------------------------------------------------

def test_missing_description_defaults_empty(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        name: nodesc
        ---
    """)

    manifest = parse_skill_file(p)

    assert manifest.description == ""


# ---------------------------------------------------------------------------
# 5. No frontmatter (no ---) → raises SkillParseError
# ---------------------------------------------------------------------------

def test_no_frontmatter_raises_error(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        # Just markdown, no frontmatter
        Hello world
    """)

    with pytest.raises(SkillParseError):
        parse_skill_file(p)


# ---------------------------------------------------------------------------
# 6. Invalid YAML syntax → raises SkillParseError
# ---------------------------------------------------------------------------

def test_invalid_yaml_raises_error(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        name: [broken
        ---
    """)

    with pytest.raises(SkillParseError):
        parse_skill_file(p)


# ---------------------------------------------------------------------------
# 7. JSON-in-YAML flow mapping format (curly braces)
# ---------------------------------------------------------------------------

def test_json_in_yaml_flow_mapping(tmp_path: Path) -> None:
    """Real skills use JSON-in-YAML flow syntax (curly braces). Verify it works."""
    p = _write_skill(tmp_path, """\
        ---
        name: github
        description: "Use gh for GitHub issues."
        metadata:
          {
            "openclaw":
              {
                "emoji": "🐙",
                "requires": { "bins": ["gh"] },
                "install":
                  [
                    {
                      "id": "brew",
                      "kind": "brew",
                      "formula": "gh",
                      "bins": ["gh"],
                      "label": "Install GitHub CLI (brew)",
                    },
                  ],
              },
          }
        ---

        # GitHub Skill
    """)

    manifest = parse_skill_file(p)

    assert manifest.name == "github"
    assert manifest.emoji == "🐙"
    assert manifest.requirements.bins == ["gh"]
    assert len(manifest.install_specs) == 1
    assert manifest.install_specs[0].kind == "brew"
    assert manifest.install_specs[0].formula == "gh"


# ---------------------------------------------------------------------------
# 8. Invalid install kind → skipped, not error
# ---------------------------------------------------------------------------

def test_invalid_install_kind_skipped(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        name: mixed-install
        metadata:
          openclaw:
            install:
              - kind: brew
                formula: valid-tool
                bins:
                  - valid-tool
              - kind: unknown_kind
                package: something
              - kind: node
                package: prettier
        ---
    """)

    manifest = parse_skill_file(p)

    # unknown_kind should be skipped; brew and node kept
    assert len(manifest.install_specs) == 2
    assert manifest.install_specs[0].kind == "brew"
    assert manifest.install_specs[0].formula == "valid-tool"
    assert manifest.install_specs[1].kind == "node"
    assert manifest.install_specs[1].package == "prettier"


# ---------------------------------------------------------------------------
# 9. Empty body → body is ""
# ---------------------------------------------------------------------------

def test_empty_body(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        name: nobody
        ---
    """)

    manifest = parse_skill_file(p)

    assert manifest.body == ""


# ---------------------------------------------------------------------------
# 10. disable-model-invocation: true is parsed
# ---------------------------------------------------------------------------

def test_disable_model_invocation(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        name: nomodel
        disable-model-invocation: true
        ---

        Body.
    """)

    manifest = parse_skill_file(p)

    assert manifest.disable_model_invocation is True


# ---------------------------------------------------------------------------
# Bonus: requirements with anyBins → any_bins, env, os
# ---------------------------------------------------------------------------

def test_requirements_any_bins_env_os(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        name: complex-reqs
        metadata:
          openclaw:
            os:
              - darwin
              - linux
            requires:
              bins:
                - git
              anyBins:
                - nvim
                - vim
              env:
                - GITHUB_TOKEN
        ---
    """)

    manifest = parse_skill_file(p)

    assert manifest.requirements.bins == ["git"]
    assert manifest.requirements.any_bins == ["nvim", "vim"]
    assert manifest.requirements.env == ["GITHUB_TOKEN"]
    assert manifest.requirements.os == ["darwin", "linux"]


# ---------------------------------------------------------------------------
# Bonus: always flag
# ---------------------------------------------------------------------------

def test_always_flag(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        name: always-on
        metadata:
          openclaw:
            always: true
        ---
    """)

    manifest = parse_skill_file(p)

    assert manifest.always is True


# ---------------------------------------------------------------------------
# Bonus: file not found
# ---------------------------------------------------------------------------

def test_file_not_found_raises_error(tmp_path: Path) -> None:
    with pytest.raises(SkillParseError):
        parse_skill_file(tmp_path / "nonexistent" / "SKILL.md")


# ---------------------------------------------------------------------------
# Bonus: install specs for all kinds
# ---------------------------------------------------------------------------

def test_install_specs_all_kinds(tmp_path: Path) -> None:
    p = _write_skill(tmp_path, """\
        ---
        name: all-kinds
        metadata:
          openclaw:
            install:
              - kind: brew
                formula: ripgrep
              - kind: node
                package: prettier
              - kind: uv
                package: ruff
              - kind: go
                module: golang.org/x/tools
              - kind: download
                url: https://example.com/tool
                bins:
                  - tool
        ---
    """)

    manifest = parse_skill_file(p)

    assert len(manifest.install_specs) == 5
    assert manifest.install_specs[0].kind == "brew"
    assert manifest.install_specs[0].formula == "ripgrep"
    assert manifest.install_specs[1].kind == "node"
    assert manifest.install_specs[1].package == "prettier"
    assert manifest.install_specs[2].kind == "uv"
    assert manifest.install_specs[2].package == "ruff"
    assert manifest.install_specs[3].kind == "go"
    assert manifest.install_specs[3].module == "golang.org/x/tools"
    assert manifest.install_specs[4].kind == "download"
    assert manifest.install_specs[4].url == "https://example.com/tool"
    assert manifest.install_specs[4].bins == ["tool"]


# ---------------------------------------------------------------------------
# Auto-generated skill metadata (SOP graduation fields)
# ---------------------------------------------------------------------------

class TestAutoGeneratedFrontmatter:
    def test_parse_auto_generated_skill(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "deploy-k8s-helm"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "---\n"
            "name: deploy-k8s-helm\n"
            "description: Deploy via Helm chart\n"
            "auto_generated: true\n"
            "lifecycle: active\n"
            'generated_at: "2026-05-20T10:00:00Z"\n'
            'source_session: "feishu:cli_xxx:ou_yyy"\n'
            "---\n\n"
            "# deploy-k8s-helm\n\n"
            "## Procedure\n"
            "1. Check helm version\n"
            "2. Run helm upgrade\n"
        )

        manifest = parse_skill_file(skill_file)

        assert manifest.auto_generated is True
        assert manifest.lifecycle == "active"
        assert manifest.generated_at == "2026-05-20T10:00:00Z"
        assert manifest.source_session == "feishu:cli_xxx:ou_yyy"

    def test_parse_legacy_skill_without_new_fields(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "old-skill"
        skill_dir.mkdir()
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(
            "---\n"
            "name: old-skill\n"
            "description: A legacy skill\n"
            "---\n\n"
            "# old-skill\n"
            "Some content.\n"
        )

        manifest = parse_skill_file(skill_file)

        assert manifest.auto_generated is False
        assert manifest.lifecycle == "active"
        assert manifest.generated_at is None
        assert manifest.source_session is None
