from __future__ import annotations

from pathlib import Path

import pytest

from pyclaw.core.agent.tools.registry import ToolContext
from pyclaw.core.agent.tools.skill_view import SkillViewTool


def _ctx() -> ToolContext:
    return ToolContext(workspace_id="default", workspace_path=Path("/tmp"), session_id="s1")


class _FakeProvider:
    def __init__(self, detail: str | None):
        self._detail = detail

    def get_skill_detail(self, name: str) -> str | None:
        if self._detail is not None:
            return self._detail
        return None


class TestSkillViewTool:
    async def test_skill_view_returns_body(self) -> None:
        provider = _FakeProvider("# Full skill body here")
        tool = SkillViewTool(provider)
        result = await tool.execute({"_call_id": "c1", "name": "my-skill"}, _ctx())
        assert not result.is_error
        assert result.content[0].text == "# Full skill body here"

    async def test_skill_view_not_found(self) -> None:
        provider = _FakeProvider(None)
        tool = SkillViewTool(provider)
        result = await tool.execute({"_call_id": "c1", "name": "nonexistent"}, _ctx())
        assert result.is_error
        assert "nonexistent" in result.content[0].text
        assert "not found" in result.content[0].text


class TestSkillViewFactoryRegistration:
    async def test_skill_view_registered_when_progressive_on(self) -> None:
        from pyclaw.core.agent.factory import create_agent_runner_deps
        from pyclaw.infra.settings import Settings
        from pyclaw.storage.session.base import InMemorySessionStore

        settings = Settings()
        settings.skills.progressive_disclosure = True
        store = InMemorySessionStore()
        deps = await create_agent_runner_deps(settings, store)
        assert "skill_view" in deps.tools

    async def test_skill_view_not_registered_when_progressive_off(self) -> None:
        from pyclaw.core.agent.factory import create_agent_runner_deps
        from pyclaw.infra.settings import Settings
        from pyclaw.storage.session.base import InMemorySessionStore

        settings = Settings()
        settings.skills.progressive_disclosure = False
        store = InMemorySessionStore()
        deps = await create_agent_runner_deps(settings, store)
        assert "skill_view" not in deps.tools
