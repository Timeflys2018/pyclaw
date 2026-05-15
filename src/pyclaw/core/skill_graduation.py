"""Skill graduation — promotes high-frequency SOPs from L3 procedures to SKILL.md files."""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")


def parse_sop_content(content: str) -> tuple[str, str, str] | None:
    """Parse L3 SOP content into (name, description, procedure).

    The stored format is: ``name\\ndescription\\nprocedure``
    (see ``sop_extraction._format_sop_content``).

    Returns None if content format is unparseable or name is invalid.
    """
    if not content or not content.strip():
        return None

    parts = content.split("\n", 2)
    if len(parts) < 3:
        logger.warning("SOP content has < 3 lines, cannot parse for graduation")
        return None

    name = parts[0].strip()
    description = parts[1].strip()
    procedure = parts[2].strip()

    if not _NAME_RE.match(name):
        logger.warning(
            "SOP name '%s' doesn't match kebab-case pattern, skipping graduation",
            name,
        )
        return None

    if not procedure:
        logger.warning("SOP '%s' has empty procedure, skipping graduation", name)
        return None

    return name, description, procedure


def generate_skill_md_template(
    name: str,
    description: str,
    procedure: str,
    session_key: str,
) -> str:
    """Generate SKILL.md content using pure template (no LLM)."""
    now_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    safe_desc = description.replace('"', '\\"')

    return f'''---
name: {name}
description: "{safe_desc}"
auto_generated: true
lifecycle: active
generated_at: "{now_iso}"
source_session: "{session_key}"
---

# {name}

{description}

## Procedure

{procedure}
'''


async def generate_skill_md_enrich(
    name: str,
    description: str,
    procedure: str,
    session_key: str,
    llm_client: Any,
    model: str | None,
) -> str:
    """Generate enriched SKILL.md via LLM. Falls back to template on failure."""
    import asyncio

    prompt = (
        "将以下 SOP 扩展为完整的 SKILL.md 格式。保持原有步骤不变，"
        "但添加 When to Use、Prerequisites、Common Issues 等 section。\n\n"
        f"SOP Name: {name}\n"
        f"Description: {description}\n"
        f"Procedure:\n{procedure}\n\n"
        "输出完整的 SKILL.md 内容（含 frontmatter）。frontmatter 必须包含:\n"
        f"name: {name}\n"
        "description: (你的改进版)\n"
        "auto_generated: true\n"
        "lifecycle: active\n"
        "generated_at: (当前时间 ISO8601)\n"
        f'source_session: "{session_key}"\n'
    )

    try:
        response = await asyncio.wait_for(
            llm_client.complete(
                messages=[{"role": "user", "content": prompt}],
                model=model,
            ),
            timeout=15.0,
        )
        if response and response.text and len(response.text) > 100:
            return response.text
    except (TimeoutError, Exception) as exc:
        logger.warning(
            "Skill enrich LLM failed for '%s': %s, falling back to template",
            name,
            exc,
        )

    return generate_skill_md_template(name, description, procedure, session_key)


def graduate_single_sop(
    entry_id: str,
    content: str,
    session_key: str,
    workspace_base_dir: Path,
    mode: str = "template",
) -> tuple[bool, str | None]:
    """Attempt to graduate a single SOP to SKILL.md.

    Returns (success: bool, skill_path: str | None).
    Does NOT update DB — caller handles that.
    """
    parsed = parse_sop_content(content)
    if parsed is None:
        return False, None

    name, description, procedure = parsed

    workspace_id = session_key.replace(":", "_")
    workspace_path = workspace_base_dir / workspace_id
    skill_dir = workspace_path / "skills" / name
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists():
        logger.debug("SKILL.md already exists at %s, skipping", skill_file)
        return False, None

    # Cross-layer collision check: skip if skill name exists from another source
    from pyclaw.skills.discovery import discover_skills

    try:
        existing_skills = discover_skills(workspace_path)
        if any(s.name == name for s in existing_skills):
            logger.info("Skill '%s' already exists from another source, skipping graduation", name)
            return False, None
    except Exception:
        pass  # Discovery failure should not block graduation

    skill_content = generate_skill_md_template(name, description, procedure, session_key)
    try:
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file.write_text(skill_content, encoding="utf-8")
    except OSError as exc:
        logger.error("Failed to write SKILL.md at %s: %s", skill_file, exc)
        return False, None

    if not skill_file.exists():
        logger.error("SKILL.md verification failed at %s", skill_file)
        return False, None

    logger.info("Graduated SOP '%s' → %s", name, skill_file)
    return True, str(skill_file)
