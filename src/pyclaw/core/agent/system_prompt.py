from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Sequence

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
    workspace_path: str | None = None
    now_iso: str | None = None
    identity: str = "You are PyClaw, a multi-channel AI assistant with tool access."


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


async def build_system_prompt(
    inputs: PromptInputs,
    hooks: HookRegistry | None = None,
    user_prompt: str | None = None,
) -> str:
    base_sections: list[str | None] = [
        identity_section(inputs),
        tooling_section(inputs),
        skills_section(inputs),
        workspace_section(inputs),
        runtime_section(inputs),
    ]

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

    ordered: list[str] = []
    if hook_prepend:
        ordered.append(hook_prepend)
    for section in base_sections:
        if section:
            ordered.append(section)
    if hook_append:
        ordered.append(hook_append)

    return "\n\n".join(ordered)
