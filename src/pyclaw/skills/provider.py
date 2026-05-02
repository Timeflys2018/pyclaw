from __future__ import annotations

import logging
from pathlib import Path

from pyclaw.infra.settings import SkillSettings
from pyclaw.skills.discovery import discover_skills
from pyclaw.skills.eligibility import filter_eligible
from pyclaw.skills.prompt import build_skills_prompt

logger = logging.getLogger(__name__)


class DefaultSkillProvider:
    def __init__(self, settings: SkillSettings | None = None) -> None:
        self._settings = settings

    def resolve_skills_prompt(self, workspace_path: str) -> str | None:
        try:
            all_skills = discover_skills(Path(workspace_path), self._settings)
            eligible = filter_eligible(all_skills)
            if eligible:
                return build_skills_prompt(eligible, self._settings)
        except Exception:
            logger.warning("Skill discovery failed", exc_info=True)
        return None
