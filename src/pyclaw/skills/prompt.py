from __future__ import annotations

import math
from pathlib import Path

from pyclaw.core.utils.xml import xml_escape
from pyclaw.infra.settings import SkillSettings
from pyclaw.skills.models import SkillManifest

_FULL_PREAMBLE = (
    "\n\nThe following skills provide specialized instructions for specific tasks.\n"
    "Use the read tool to load a skill's file when the task matches its description.\n"
    "When a skill file references a relative path, resolve it against the skill "
    "directory (parent of SKILL.md / dirname of the path) and use that absolute "
    "path in tool commands."
)

_COMPACT_PREAMBLE = (
    "\n\nThe following skills provide specialized instructions for specific tasks.\n"
    "Use the read tool to load a skill's file when the task matches its name.\n"
    "When a skill file references a relative path, resolve it against the skill "
    "directory (parent of SKILL.md / dirname of the path) and use that absolute "
    "path in tool commands."
)

_INDEX_PREAMBLE = (
    "\n\nThe following skills provide specialized instructions for specific tasks.\n"
    "If a task matches a skill below, use the `skill_view` tool to load its full instructions."
)

_OVERHEAD_RESERVE = 150


def _compact_home_path(file_path: str) -> str:
    home = str(Path.home())
    prefix = home + "/"
    if file_path.startswith(prefix):
        return "~/" + file_path[len(prefix) :]
    return file_path


def _render_skill_full(skill: SkillManifest) -> str:
    lines = [
        "  <skill>",
        "    <name>" + xml_escape(skill.name) + "</name>",
        "    <description>" + xml_escape(skill.description) + "</description>",
        "    <location>" + xml_escape(skill.file_path) + "</location>",
        "  </skill>",
    ]
    return "\n".join(lines)


def _render_skill_compact(skill: SkillManifest) -> str:
    lines = [
        "  <skill>",
        "    <name>" + xml_escape(skill.name) + "</name>",
        "    <location>" + xml_escape(skill.file_path) + "</location>",
        "  </skill>",
    ]
    return "\n".join(lines)


def format_skills_full(skills: list[SkillManifest]) -> str:
    if not skills:
        return ""
    parts = [_FULL_PREAMBLE, "", "<available_skills>"]
    for skill in skills:
        parts.append(_render_skill_full(skill))
    parts.append("</available_skills>")
    return "\n".join(parts)


def _render_skill_index(skill: SkillManifest) -> str:
    lines = [
        "  <skill>",
        "    <name>" + xml_escape(skill.name) + "</name>",
        "    <description>" + xml_escape(skill.description) + "</description>",
        "  </skill>",
    ]
    return "\n".join(lines)


def format_skills_index(skills: list[SkillManifest]) -> str:
    if not skills:
        return ""
    parts = [_INDEX_PREAMBLE, "", "<available_skills>"]
    for skill in skills:
        parts.append(_render_skill_index(skill))
    parts.append("</available_skills>")
    return "\n".join(parts)


def format_skills_compact(skills: list[SkillManifest]) -> str:
    if not skills:
        return ""
    parts = [_COMPACT_PREAMBLE, "", "<available_skills>"]
    for skill in skills:
        parts.append(_render_skill_compact(skill))
    parts.append("</available_skills>")
    return "\n".join(parts)


def build_skills_prompt(
    skills: list[SkillManifest],
    settings: SkillSettings | None = None,
) -> str:
    if not skills:
        return ""
    if settings is None:
        settings = SkillSettings()

    sorted_skills = sorted(skills, key=lambda s: s.name)

    compacted = [
        SkillManifest(
            name=s.name,
            description=s.description,
            file_path=_compact_home_path(s.file_path),
        )
        for s in sorted_skills
    ]

    total = len(compacted)
    truncated = False
    compact = False

    if len(compacted) > settings.max_skills_in_prompt:
        compacted = compacted[: settings.max_skills_in_prompt]
        truncated = True

    full_text = format_skills_full(compacted)
    if len(full_text) <= settings.max_skills_prompt_chars:
        rendered = full_text
    else:
        compact = True
        compact_budget = settings.max_skills_prompt_chars - _OVERHEAD_RESERVE
        compact_text = format_skills_compact(compacted)
        if len(compact_text) <= compact_budget:
            rendered = compact_text
        else:
            truncated = True
            lo, hi = 0, len(compacted)
            best = 0
            while lo <= hi:
                mid = math.ceil((lo + hi) / 2)
                if mid == 0:
                    lo = mid + 1
                    continue
                candidate = format_skills_compact(compacted[:mid])
                if len(candidate) <= compact_budget:
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            rendered = format_skills_compact(compacted[:best]) if best > 0 else ""

    included = rendered.count("<skill>")
    warning = ""
    if truncated and compact:
        warning = (
            "\u26a0\ufe0f Skills truncated: included "
            + str(included)
            + " of "
            + str(total)
            + " (compact format, descriptions omitted)."
        )
    elif compact:
        warning = "\u26a0\ufe0f Skills catalog using compact format (descriptions omitted)."

    if warning and rendered:
        return warning + "\n" + rendered
    if warning:
        return warning
    return rendered
