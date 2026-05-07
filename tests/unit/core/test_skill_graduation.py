from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from pyclaw.core.skill_graduation import (
    generate_skill_md_enrich,
    generate_skill_md_template,
    graduate_single_sop,
    parse_sop_content,
)


class TestParseSopContent:
    def test_valid_three_line_content(self):
        content = "deploy-k8s-helm\nDeploy via Helm\n1. Build image\n2. Push\n3. Apply"
        result = parse_sop_content(content)
        assert result is not None
        name, description, procedure = result
        assert name == "deploy-k8s-helm"
        assert description == "Deploy via Helm"
        assert procedure == "1. Build image\n2. Push\n3. Apply"

    def test_empty_content(self):
        assert parse_sop_content("") is None
        assert parse_sop_content("   ") is None

    def test_fewer_than_three_lines(self):
        assert parse_sop_content("only-name\ndescription") is None
        assert parse_sop_content("single-line") is None

    def test_invalid_name_uppercase(self):
        content = "Deploy-Helm\nSome desc\n1. Step"
        assert parse_sop_content(content) is None

    def test_invalid_name_spaces(self):
        content = "deploy helm\nSome desc\n1. Step"
        assert parse_sop_content(content) is None

    def test_invalid_name_too_short(self):
        content = "a\nSome desc\n1. Step"
        assert parse_sop_content(content) is None

    def test_empty_procedure(self):
        content = "valid-name\nSome desc\n   "
        assert parse_sop_content(content) is None

    def test_multiline_procedure_preserved(self):
        procedure_text = "1. First step\n2. Second step\n3. Third step with details"
        content = f"my-sop\nDoes things\n{procedure_text}"
        result = parse_sop_content(content)
        assert result is not None
        assert result[2] == procedure_text

    def test_strips_whitespace(self):
        content = "  valid-name  \n  Some desc  \n  1. Step  "
        result = parse_sop_content(content)
        assert result is not None
        assert result[0] == "valid-name"
        assert result[1] == "Some desc"
        assert result[2] == "1. Step"


class TestGenerateSkillMdTemplate:
    def test_contains_frontmatter_fields(self):
        result = generate_skill_md_template(
            "test-skill", "A test skill", "1. Do thing", "feishu:cli_xxx:ou_yyy"
        )
        assert result.startswith("---\n")
        assert "name: test-skill" in result
        assert 'description: "A test skill"' in result
        assert "auto_generated: true" in result
        assert "lifecycle: active" in result
        assert "generated_at:" in result
        assert 'source_session: "feishu:cli_xxx:ou_yyy"' in result

    def test_contains_body(self):
        result = generate_skill_md_template(
            "my-skill", "Does X", "1. Step A\n2. Step B", "s:k:u"
        )
        assert "# my-skill" in result
        assert "Does X" in result
        assert "## Procedure" in result
        assert "1. Step A\n2. Step B" in result

    def test_parseable_by_skill_parser(self, tmp_path: Path):
        result = generate_skill_md_template(
            "parseable-skill", "Skill for testing", "1. Do X\n2. Do Y", "test:session:key"
        )
        skill_file = tmp_path / "SKILL.md"
        skill_file.write_text(result, encoding="utf-8")

        from pyclaw.skills.parser import parse_skill_file

        manifest = parse_skill_file(skill_file)
        assert manifest.name == "parseable-skill"
        assert manifest.description == "Skill for testing"
        assert manifest.auto_generated is True
        assert manifest.lifecycle == "active"
        assert manifest.source_session == "test:session:key"


class TestGenerateSkillMdEnrich:
    def test_success_returns_llm_result(self):
        from dataclasses import dataclass

        @dataclass
        class FakeLLMResponse:
            text: str

        llm_client = AsyncMock()
        llm_client.complete.return_value = FakeLLMResponse(
            text="---\nname: enriched\n---\n" + "x" * 200
        )

        result = asyncio.run(
            generate_skill_md_enrich(
                "my-sop", "desc", "1. Step", "s:k:u", llm_client, "gpt-4"
            )
        )
        assert "enriched" in result
        llm_client.complete.assert_awaited_once()

    def test_fallback_on_short_result(self):
        from dataclasses import dataclass

        @dataclass
        class FakeLLMResponse:
            text: str

        llm_client = AsyncMock()
        llm_client.complete.return_value = FakeLLMResponse(text="short")

        result = asyncio.run(
            generate_skill_md_enrich(
                "my-sop", "desc", "1. Step", "s:k:u", llm_client, None
            )
        )
        assert "name: my-sop" in result
        assert "## Procedure" in result

    def test_fallback_on_exception(self):
        llm_client = AsyncMock()
        llm_client.complete.side_effect = RuntimeError("connection failed")

        result = asyncio.run(
            generate_skill_md_enrich(
                "my-sop", "desc", "1. Step", "s:k:u", llm_client, None
            )
        )
        assert "name: my-sop" in result

    def test_fallback_on_timeout(self):
        async def slow_complete(*args, **kwargs):
            await asyncio.sleep(30)
            return "never returned"

        llm_client = AsyncMock()
        llm_client.complete = slow_complete

        result = asyncio.run(
            generate_skill_md_enrich(
                "my-sop", "desc", "1. Step", "s:k:u", llm_client, None
            )
        )
        assert "name: my-sop" in result


class TestGraduateSingleSop:
    def test_success_writes_file(self, tmp_path: Path):
        content = "deploy-app\nDeploy application\n1. Build\n2. Push\n3. Deploy"
        success, path = graduate_single_sop(
            entry_id="uuid-123",
            content=content,
            session_key="feishu:cli_xxx:ou_yyy",
            workspace_base_dir=tmp_path,
        )
        assert success is True
        assert path is not None
        skill_file = Path(path)
        assert skill_file.exists()
        text = skill_file.read_text(encoding="utf-8")
        assert "name: deploy-app" in text
        assert "1. Build\n2. Push\n3. Deploy" in text

    def test_collision_skips(self, tmp_path: Path):
        content = "deploy-app\nDeploy application\n1. Build\n2. Push"
        workspace_id = "feishu_cli_xxx_ou_yyy"
        skill_dir = tmp_path / workspace_id / "skills" / "deploy-app"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("existing", encoding="utf-8")

        success, path = graduate_single_sop(
            entry_id="uuid-456",
            content=content,
            session_key="feishu:cli_xxx:ou_yyy",
            workspace_base_dir=tmp_path,
        )
        assert success is False
        assert path is None

    def test_invalid_content_returns_false(self, tmp_path: Path):
        success, path = graduate_single_sop(
            entry_id="uuid-789",
            content="Invalid Name\nDesc\n1. Step",
            session_key="s:k:u",
            workspace_base_dir=tmp_path,
        )
        assert success is False
        assert path is None

    def test_write_failure(self, tmp_path: Path):
        content = "write-fail\nTest write failure\n1. Step"
        with patch("pathlib.Path.mkdir", side_effect=OSError("permission denied")):
            success, path = graduate_single_sop(
                entry_id="uuid-000",
                content=content,
                session_key="test:key:user",
                workspace_base_dir=tmp_path,
            )
        assert success is False
        assert path is None

    def test_workspace_id_derivation(self, tmp_path: Path):
        content = "my-sop\nDescription\n1. Step one"
        success, path = graduate_single_sop(
            entry_id="uuid-111",
            content=content,
            session_key="feishu:cli_abc:ou_xyz",
            workspace_base_dir=tmp_path,
        )
        assert success is True
        assert "feishu_cli_abc_ou_xyz" in path
        assert "/skills/my-sop/SKILL.md" in path
