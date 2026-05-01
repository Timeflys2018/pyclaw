from __future__ import annotations

import logging
from pathlib import Path

from pyclaw.infra.settings import SkillSettings
from pyclaw.skills.models import SkillManifest, SkillParseError
from pyclaw.skills.parser import parse_skill_file

logger = logging.getLogger(__name__)

_BLOCKED_NAMES = frozenset({"node_modules"})


def _is_valid_skill_dir(entry_name: str) -> bool:
    if entry_name.startswith("."):
        return False
    return entry_name not in _BLOCKED_NAMES


def _validate_path_containment(skill_path: Path, root: Path) -> bool:
    try:
        resolved = skill_path.resolve()
        root_resolved = root.resolve()
        return resolved.is_relative_to(root_resolved)
    except (OSError, ValueError):
        return False


def _scan_directory(
    root: Path,
    max_file_bytes: int,
    max_candidates: int,
    max_skills_loaded: int,
) -> list[SkillManifest]:
    try:
        entries = sorted(
            [e.name for e in root.iterdir() if e.is_dir()],
        )
    except OSError:
        logger.warning("Cannot list directory: %s", root)
        return []

    valid = [name for name in entries if _is_valid_skill_dir(name)]

    if len(valid) > max_candidates:
        logger.warning(
            "Directory %s has %d candidates, capping at %d",
            root,
            len(valid),
            max_candidates,
        )
        valid = valid[:max_candidates]

    results: list[SkillManifest] = []
    for name in valid:
        if len(results) >= max_skills_loaded:
            break

        skill_file = root / name / "SKILL.md"
        if not skill_file.exists():
            continue

        try:
            size = skill_file.stat().st_size
        except OSError:
            logger.warning("Cannot stat %s, skipping", skill_file)
            continue

        if size > max_file_bytes:
            logger.warning(
                "Skill %s SKILL.md is %d bytes (limit %d), skipping",
                name,
                size,
                max_file_bytes,
            )
            continue

        if not _validate_path_containment(skill_file, root):
            logger.warning(
                "Skill %s escapes root directory %s, skipping",
                name,
                root,
            )
            continue

        try:
            manifest = parse_skill_file(skill_file)
        except SkillParseError as exc:
            logger.warning("Failed to parse %s: %s", skill_file, exc)
            continue

        results.append(manifest)

    return results


def discover_skills(
    workspace_path: str | Path,
    settings: SkillSettings | None = None,
) -> list[SkillManifest]:
    if settings is None:
        settings = SkillSettings()

    ws = Path(workspace_path)

    sources: list[tuple[str, Path | None]] = [
        (
            "bundled",
            Path(settings.bundled_skills_dir) if settings.bundled_skills_dir else None,
        ),
        (
            "personal-agents",
            Path(settings.personal_agents_skills_dir).expanduser(),
        ),
        (
            "managed",
            Path(settings.managed_skills_dir).expanduser(),
        ),
        (
            "project-agents",
            ws / settings.project_agents_skills_dir,
        ),
        (
            "workspace",
            ws / settings.workspace_skills_dir,
        ),
    ]

    merged: dict[str, SkillManifest] = {}

    for _label, directory in sources:
        if directory is None:
            continue
        if not directory.is_dir():
            continue

        skills = _scan_directory(
            directory,
            max_file_bytes=settings.max_skill_file_bytes,
            max_candidates=settings.max_candidates_per_root,
            max_skills_loaded=settings.max_skills_loaded_per_source,
        )
        for skill in skills:
            merged[skill.name] = skill

    return sorted(merged.values(), key=lambda s: s.name)
