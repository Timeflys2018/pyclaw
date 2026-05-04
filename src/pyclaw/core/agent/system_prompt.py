from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Sequence

logger = logging.getLogger(__name__)

from pyclaw.core.hooks import HookRegistry, PromptBuildContext


@dataclass
class SkillSummary:
    name: str
    description: str
    location: str


@dataclass
class PromptInputs:
    session_id: str
    workspace_id: str
    agent_id: str
    model: str
    tools: Sequence[tuple[str, str]] = ()
    skills: Sequence[SkillSummary] = ()
    skills_prompt: str | None = None
    workspace_path: str | None = None
    now_iso: str | None = None
    identity: str = "You are PyClaw, a multi-channel AI assistant with tool access."


@dataclass
class PromptSection:
    name: str
    text: str
    truncatable: bool = True
    estimated_tokens: int = field(init=False)

    def __post_init__(self) -> None:
        self.estimated_tokens = len(self.text) // 4


@dataclass
class SystemPromptResult:
    text: str
    sections: list[PromptSection]
    token_breakdown: dict[str, int]

    @classmethod
    def from_sections(cls, sections: list[PromptSection]) -> SystemPromptResult:
        text = "\n\n".join(s.text for s in sections)
        token_breakdown = {s.name: s.estimated_tokens for s in sections}
        return cls(text=text, sections=list(sections), token_breakdown=token_breakdown)


def identity_section(inputs: PromptInputs) -> str:
    return inputs.identity


def tooling_section(inputs: PromptInputs) -> str | None:
    if not inputs.tools:
        return None
    lines = ["## Tools", "You have access to the following tools. Use them when helpful."]
    for name, desc in inputs.tools:
        desc_short = (desc or "").split("\n", 1)[0].strip()
        lines.append(f"- `{name}` — {desc_short}")
    return "\n".join(lines)


def skills_section(inputs: PromptInputs) -> str | None:
    if not inputs.skills:
        return None
    lines = [
        "## Available Skills",
        "Before replying, scan the skill descriptions below. "
        "If one applies, read its SKILL.md file at the location with the read tool, then follow it.",
        "",
        "<available_skills>",
    ]
    for skill in inputs.skills:
        lines.append("  <skill>")
        lines.append(f"    <name>{_xml_escape(skill.name)}</name>")
        lines.append(f"    <description>{_xml_escape(skill.description)}</description>")
        lines.append(f"    <location>{_xml_escape(skill.location)}</location>")
        lines.append("  </skill>")
    lines.append("</available_skills>")
    return "\n".join(lines)


def workspace_section(inputs: PromptInputs) -> str | None:
    if not inputs.workspace_path:
        return None
    return (
        "## Workspace\n"
        f"Working directory: `{inputs.workspace_path}`\n"
        f"Workspace id: `{inputs.workspace_id}`"
    )


def runtime_section(inputs: PromptInputs) -> str:
    now = inputs.now_iso or datetime.now(timezone.utc).isoformat()
    return (
        "## Runtime\n"
        f"agent={inputs.agent_id} | model={inputs.model} | session={inputs.session_id} | time={now}"
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


_TRUNCATION_PRIORITY = ["skills", "bootstrap", "workspace"]


def enforce_system_budget(
    sections: list[PromptSection], budget: int
) -> list[PromptSection]:
    total = sum(s.estimated_tokens for s in sections)
    if total <= budget:
        return sections

    overflow = total - budget
    result = list(sections)

    for priority_name in _TRUNCATION_PRIORITY:
        if overflow <= 0:
            break
        for i, section in enumerate(result):
            if section.name != priority_name or not section.truncatable:
                continue
            if section.estimated_tokens <= overflow:
                logger.warning(
                    "system_zone over budget: removed %s (%d tokens)",
                    section.name,
                    section.estimated_tokens,
                )
                overflow -= section.estimated_tokens
                result[i] = PromptSection(
                    name=section.name, text="", truncatable=section.truncatable
                )
            else:
                keep_chars = max(0, len(section.text) - overflow * 4)
                truncated_text = section.text[:keep_chars]
                logger.warning(
                    "system_zone over budget: truncated %s from %d to %d tokens",
                    section.name,
                    section.estimated_tokens,
                    len(truncated_text) // 4,
                )
                result[i] = PromptSection(
                    name=section.name, text=truncated_text, truncatable=section.truncatable
                )
                overflow = 0
            break

    if overflow > 0:
        logger.warning(
            "system_zone still over budget by %d tokens "
            "after truncation (non-truncatable sections)",
            overflow,
        )

    return [s for s in result if s.text]


def build_frozen_prefix(inputs: PromptInputs, budget: int | None = None) -> SystemPromptResult:
    sections: list[PromptSection] = []

    identity_text = identity_section(inputs)
    if identity_text:
        sections.append(PromptSection(name="identity", text=identity_text, truncatable=False))

    tools_text = tooling_section(inputs)
    if tools_text:
        sections.append(PromptSection(name="tools", text=tools_text, truncatable=False))

    skills_text = inputs.skills_prompt if inputs.skills_prompt else skills_section(inputs)
    if skills_text:
        sections.append(PromptSection(name="skills", text=skills_text, truncatable=True))

    ws_text = workspace_section(inputs)
    if ws_text:
        sections.append(PromptSection(name="workspace", text=ws_text, truncatable=True))

    if budget is not None:
        sections = enforce_system_budget(sections, budget)

    return SystemPromptResult.from_sections(sections)


async def build_per_turn_suffix(
    inputs: PromptInputs,
    hooks: HookRegistry | None = None,
    user_prompt: str | None = None,
) -> SystemPromptResult:
    sections: list[PromptSection] = []

    hook_prepend: str | None = None
    hook_append: str | None = None
    if hooks is not None:
        ctx = PromptBuildContext(
            session_id=inputs.session_id,
            workspace_id=inputs.workspace_id,
            agent_id=inputs.agent_id,
            available_tools=[name for name, _ in inputs.tools],
            prompt=user_prompt,
        )
        additions = await hooks.collect_prompt_additions(ctx)
        hook_prepend = additions.prepend
        hook_append = additions.append

    if hook_prepend:
        sections.append(PromptSection(name="hooks_prepend", text=hook_prepend, truncatable=True))

    sections.append(PromptSection(name="runtime", text=runtime_section(inputs), truncatable=True))

    if hook_append:
        sections.append(PromptSection(name="hooks_append", text=hook_append, truncatable=True))

    return SystemPromptResult.from_sections(sections)


async def build_system_prompt(
    inputs: PromptInputs,
    hooks: HookRegistry | None = None,
    user_prompt: str | None = None,
) -> str:
    hook_prepend: str | None = None
    hook_append: str | None = None
    if hooks is not None:
        ctx = PromptBuildContext(
            session_id=inputs.session_id,
            workspace_id=inputs.workspace_id,
            agent_id=inputs.agent_id,
            available_tools=[name for name, _ in inputs.tools],
            prompt=user_prompt,
        )
        additions = await hooks.collect_prompt_additions(ctx)
        hook_prepend = additions.prepend
        hook_append = additions.append

    base_sections: list[str | None] = [
        identity_section(inputs),
        tooling_section(inputs),
        inputs.skills_prompt if inputs.skills_prompt else skills_section(inputs),
        workspace_section(inputs),
        runtime_section(inputs),
    ]

    ordered: list[str] = []
    if hook_prepend:
        ordered.append(hook_prepend)
    for section in base_sections:
        if section:
            ordered.append(section)
    if hook_append:
        ordered.append(hook_append)

    return "\n\n".join(ordered)
