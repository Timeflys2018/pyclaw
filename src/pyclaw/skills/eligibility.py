from __future__ import annotations

import os
import shutil
import sys

from pyclaw.skills.models import SkillManifest


def check_os(os_list: list[str]) -> bool:
    if not os_list:
        return True
    return sys.platform in os_list


def check_bins(bins: list[str]) -> bool:
    if not bins:
        return True
    return all(shutil.which(b) is not None for b in bins)


def check_any_bins(any_bins: list[str]) -> bool:
    if not any_bins:
        return True
    return any(shutil.which(b) is not None for b in any_bins)


def check_env(env_vars: list[str]) -> bool:
    if not env_vars:
        return True
    return all(os.environ.get(e) for e in env_vars)


def is_eligible(skill: SkillManifest) -> bool:
    reqs = skill.requirements

    if reqs.os and not check_os(reqs.os):
        return False

    if skill.always:
        return True

    if reqs.bins and not check_bins(reqs.bins):
        return False

    if reqs.any_bins and not check_any_bins(reqs.any_bins):
        return False

    if reqs.env and not check_env(reqs.env):
        return False

    return True


def filter_eligible(skills: list[SkillManifest]) -> list[SkillManifest]:
    return [s for s in skills if is_eligible(s) and not s.disable_model_invocation]
