"""Skill management pure ops shared by CLI and Chat handlers (Phase A3-skills)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pyclaw.skills.clawhub_client import ClawHubClient, create_client
from pyclaw.skills.discovery import discover_skills
from pyclaw.skills.eligibility import (
    check_any_bins,
    check_bins,
    check_env,
    check_os,
)
from pyclaw.skills.installer import install_skill
from pyclaw.skills.models import ClawHubError, SkillInstallError, SkillManifest


@dataclass
class DiscoveredSkill:
    name: str
    emoji: str | None
    description: str
    eligible: bool
    location: str


@dataclass
class HubSearchResult:
    slug: str
    latest_version: str
    description: str


@dataclass
class InstallResult:
    ok: bool
    dest: str | None
    error: str | None = None


@dataclass
class EligibilityReport:
    name: str
    ok: bool
    issues: list[str]


def _eligibility_issues(manifest: SkillManifest) -> list[str]:
    issues: list[str] = []
    req = manifest.requirements
    if req.os and not check_os(req.os):
        issues.append(f"os not in {req.os}")
    if req.bins and not check_bins(req.bins):
        issues.append(f"missing bins: {req.bins}")
    if req.any_bins and not check_any_bins(req.any_bins):
        issues.append(f"no any_bins available from {req.any_bins}")
    if req.env and not check_env(req.env):
        issues.append(f"missing env vars: {req.env}")
    return issues


def list_discovered(workspace_path: Path, settings: Any) -> list[DiscoveredSkill]:
    manifests = discover_skills(workspace_path, settings)
    results: list[DiscoveredSkill] = []
    for m in manifests:
        issues = _eligibility_issues(m)
        results.append(
            DiscoveredSkill(
                name=m.name,
                emoji=m.emoji,
                description=m.description[:200] if m.description else "",
                eligible=len(issues) == 0,
                location=m.file_path,
            )
        )
    return results


async def search_hub(
    query: str, *, client: ClawHubClient | None = None
) -> list[HubSearchResult]:
    if client is None:
        client = await create_client()
    try:
        hits = await client.search(query)
    except ClawHubError as exc:
        raise RuntimeError(f"ClawHub search failed: {exc}") from exc

    results: list[HubSearchResult] = []
    for hit in hits:
        slug = getattr(hit, "slug", "") or ""
        version = getattr(hit, "version", None) or getattr(hit, "latest_version", "") or ""
        description = getattr(hit, "description", "") or ""
        results.append(HubSearchResult(
            slug=str(slug),
            latest_version=str(version),
            description=str(description)[:200],
        ))
    return results


async def install(
    slug: str,
    version: str | None,
    install_dir: Path,
    *,
    client: ClawHubClient | None = None,
) -> InstallResult:
    if client is None:
        client = await create_client()
    try:
        dest = await install_skill(client, slug, version, install_dir)
        return InstallResult(ok=True, dest=str(dest))
    except (ClawHubError, SkillInstallError) as exc:
        return InstallResult(ok=False, dest=None, error=str(exc))
    except Exception as exc:
        return InstallResult(ok=False, dest=None, error=f"unexpected: {exc}")


def check_eligibility(
    workspace_path: Path, settings: Any, name: str | None = None
) -> list[EligibilityReport]:
    manifests = discover_skills(workspace_path, settings)
    if name is not None:
        manifests = [m for m in manifests if m.name == name]

    reports: list[EligibilityReport] = []
    for m in manifests:
        issues = _eligibility_issues(m)
        reports.append(EligibilityReport(name=m.name, ok=len(issues) == 0, issues=issues))
    return reports
