from __future__ import annotations

from pyclaw.core.agent.system_prompt import (
    PromptInputs,
    SkillSummary,
    SystemPromptResult,
    build_frozen_prefix,
    build_per_turn_suffix,
    build_system_prompt,
)
from pyclaw.core.hooks import (
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
        prompt = await build_system_prompt(_base_inputs(tools=[("bash", "Line one.\nLine two.")]))
        assert "Line one." in prompt
        assert "Line two." not in prompt


class TestSkillsSection:
    async def test_emits_available_skills_xml(self) -> None:
        skills = [
            SkillSummary(
                name="github", description="GitHub ops", location="~/skills/github/SKILL.md"
            )
        ]
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


class TestBuildFrozenPrefix:
    def test_all_sections(self) -> None:
        inputs = _base_inputs(
            tools=[("bash", "Run shell command")],
            skills=[
                SkillSummary(name="git", description="Git ops", location="~/skills/git/SKILL.md")
            ],
            workspace_path="/tmp/ws",
        )
        result = build_frozen_prefix(inputs)
        assert "PyClaw" in result.text
        assert "## Tools" in result.text
        assert "<available_skills>" in result.text
        assert "## Workspace" in result.text
        assert "## Runtime" not in result.text
        assert len(result.sections) == 4

    def test_minimal(self) -> None:
        inputs = _base_inputs()
        result = build_frozen_prefix(inputs)
        assert "PyClaw" in result.text
        assert "## Tools" not in result.text
        assert "<available_skills>" not in result.text
        assert "## Workspace" not in result.text
        assert len(result.sections) == 1

    def test_returns_system_prompt_result(self) -> None:
        inputs = _base_inputs(tools=[("bash", "Run shell")])
        result = build_frozen_prefix(inputs)
        assert isinstance(result, SystemPromptResult)
        assert "identity" in result.token_breakdown
        assert "tools" in result.token_breakdown

    def test_truncatable_flags(self) -> None:
        inputs = _base_inputs(
            tools=[("bash", "Run shell command")],
            skills=[SkillSummary(name="git", description="Git ops", location="loc")],
            workspace_path="/tmp/ws",
        )
        result = build_frozen_prefix(inputs)
        by_name = {s.name: s for s in result.sections}
        assert by_name["identity"].truncatable is False
        assert by_name["tools"].truncatable is False
        assert by_name["skills"].truncatable is True
        assert by_name["workspace"].truncatable is True


class TestBuildPerTurnSuffix:
    async def test_with_hooks(self) -> None:
        class _Hook:
            async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult:
                return PromptBuildResult(prepend="HOOK_PRE", append="HOOK_POST")

            async def after_response(self, observation: ResponseObservation) -> None:
                return None

        hooks = HookRegistry()
        hooks.register(_Hook())
        inputs = _base_inputs()
        result = await build_per_turn_suffix(inputs, hooks=hooks, user_prompt="hi")
        assert "HOOK_PRE" in result.text
        assert "HOOK_POST" in result.text
        assert "## Runtime" in result.text
        by_name = {s.name: s for s in result.sections}
        assert "hooks_prepend" in by_name
        assert "hooks_append" in by_name
        assert "runtime" in by_name

    async def test_empty_hooks(self) -> None:
        inputs = _base_inputs()
        result = await build_per_turn_suffix(inputs)
        assert "## Runtime" in result.text
        assert len(result.sections) == 1
        assert result.sections[0].name == "runtime"


class TestBuildSystemPromptBackwardCompat:
    async def test_basic_case(self) -> None:
        inputs = _base_inputs(
            tools=[("bash", "Run shell")],
            workspace_path="/tmp/ws",
        )
        prompt = await build_system_prompt(inputs)
        assert "PyClaw" in prompt
        assert "## Tools" in prompt
        assert "## Workspace" in prompt
        assert "## Runtime" in prompt
        parts = prompt.split("\n\n")
        identity_idx = next(i for i, p in enumerate(parts) if "PyClaw" in p)
        runtime_idx = next(i for i, p in enumerate(parts) if "## Runtime" in p)
        assert identity_idx < runtime_idx

    async def test_hook_ordering_preserved(self) -> None:
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
