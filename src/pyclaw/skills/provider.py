from __future__ import annotations

import logging
from pathlib import Path

from pyclaw.infra.settings import SkillSettings
from pyclaw.skills.discovery import discover_skills
from pyclaw.skills.eligibility import filter_eligible
from pyclaw.skills.models import SkillManifest
from pyclaw.skills.prompt import build_skills_prompt, format_skills_index

logger = logging.getLogger(__name__)


class DefaultSkillProvider:
    def __init__(self, settings: SkillSettings | None = None) -> None:
        self._settings = settings
        self._skills_cache: dict[str, SkillManifest] = {}

    def resolve_skills_prompt(self, workspace_path: str) -> str | None:
        try:
            all_skills = discover_skills(Path(workspace_path), self._settings)
            eligible = filter_eligible(all_skills)
            self._skills_cache = {s.name: s for s in eligible}
            if eligible:
                if self._settings and self._settings.progressive_disclosure:
                    return format_skills_index(eligible)
                return build_skills_prompt(eligible, self._settings)
        except Exception:
            logger.warning("Skill discovery failed", exc_info=True)
        return None

    def get_skill_detail(self, name: str) -> str | None:
        skill = self._skills_cache.get(name)
        if skill is None:
            return None
        return skill.body
