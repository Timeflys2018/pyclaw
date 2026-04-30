from __future__ import annotations

from pyclaw.core.agent.system_prompt import (
    PromptInputs,
    SkillSummary,
    build_system_prompt,
)
from pyclaw.core.hooks import (
    AgentHook,
    HookRegistry,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)


def _base_inputs(**overrides) -> PromptInputs:
    defaults = dict(
        session_id="s1",
        workspace_id="default",
        agent_id="main",
        model="gpt-4o-mini",
        now_iso="2026-01-01T00:00:00+00:00",
    )
    defaults.update(overrides)
    return PromptInputs(**defaults)


class TestMinimalPrompt:
    async def test_includes_identity_and_runtime(self) -> None:
        prompt = await build_system_prompt(_base_inputs())
        assert "PyClaw" in prompt
        assert "## Runtime" in prompt
        assert "main" in prompt
        assert "gpt-4o-mini" in prompt
        assert "## Tools" not in prompt
        assert "## Available Skills" not in prompt


class TestToolingSection:
    async def test_lists_tools(self) -> None:
        prompt = await build_system_prompt(
            _base_inputs(tools=[("bash", "Run shell command"), ("read", "Read file")])
        )
        assert "## Tools" in prompt
        assert "`bash`" in prompt
        assert "`read`" in prompt
        assert "Run shell command" in prompt

    async def test_tool_description_uses_first_line(self) -> None:
        prompt = await build_system_prompt(
            _base_inputs(tools=[("bash", "Line one.\nLine two.")])
        )
        assert "Line one." in prompt
        assert "Line two." not in prompt


class TestSkillsSection:
    async def test_emits_available_skills_xml(self) -> None:
        skills = [SkillSummary(name="github", description="GitHub ops", location="~/skills/github/SKILL.md")]
        prompt = await build_system_prompt(_base_inputs(skills=skills))
        assert "<available_skills>" in prompt
        assert "<name>github</name>" in prompt
        assert "<location>~/skills/github/SKILL.md</location>" in prompt

    async def test_escapes_xml_special_chars(self) -> None:
        skills = [SkillSummary(name="a<b", description="x & y", location="p")]
        prompt = await build_system_prompt(_base_inputs(skills=skills))
        assert "a&lt;b" in prompt
        assert "x &amp; y" in prompt


class TestHookInjection:
    async def test_prepend_and_append(self) -> None:
        class _Hook:
            async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult:
                return PromptBuildResult(prepend="MEMORY BEFORE", append="MEMORY AFTER")

            async def after_response(self, observation: ResponseObservation) -> None:
                return None

        hooks = HookRegistry()
        hooks.register(_Hook())
        prompt = await build_system_prompt(_base_inputs(), hooks=hooks)

        assert prompt.startswith("MEMORY BEFORE")
        assert prompt.endswith("MEMORY AFTER")
        assert "PyClaw" in prompt

    async def test_hook_receives_user_prompt(self) -> None:
        seen: dict[str, str | None] = {}

        class _Hook:
            async def before_prompt_build(self, context: PromptBuildContext):
                seen["prompt"] = context.prompt
                return None

            async def after_response(self, observation: ResponseObservation) -> None:
                return None

        hooks = HookRegistry()
        hooks.register(_Hook())
        await build_system_prompt(_base_inputs(), hooks=hooks, user_prompt="hello!")
        assert seen["prompt"] == "hello!"
