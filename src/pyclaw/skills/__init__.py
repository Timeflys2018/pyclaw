"""Skill Hub compatibility - ClawHub client, SKILL.md parsing, discovery."""

from pyclaw.skills.clawhub_client import ClawHubClient, create_client
from pyclaw.skills.discovery import discover_skills
from pyclaw.skills.eligibility import filter_eligible, is_eligible
from pyclaw.skills.installer import install_skill, update_lock_json, write_origin_json
from pyclaw.skills.models import (
    ClawHubError,
    InstallSpec,
    SkillInstallError,
    SkillManifest,
    SkillParseError,
    SkillRequirements,
)
from pyclaw.skills.parser import parse_skill_file
from pyclaw.skills.prompt import build_skills_prompt, format_skills_compact, format_skills_full

__all__ = [
    "ClawHubClient",
    "ClawHubError",
    "InstallSpec",
    "SkillInstallError",
    "SkillManifest",
    "SkillParseError",
    "SkillRequirements",
    "build_skills_prompt",
    "create_client",
    "discover_skills",
    "filter_eligible",
    "format_skills_compact",
    "format_skills_full",
    "install_skill",
    "is_eligible",
    "parse_skill_file",
    "update_lock_json",
    "write_origin_json",
]
