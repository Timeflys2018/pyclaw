"""Tests for skills/management pure ops (Phase A3-skills)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.skills.management import (
    InstallResult,
    check_eligibility,
    install,
    list_discovered,
    search_hub,
)
from pyclaw.skills.models import SkillManifest, SkillRequirements


def _manifest(
    name: str,
    *,
    description: str = "desc",
    bins: list[str] | None = None,
    emoji: str | None = None,
) -> SkillManifest:
    return SkillManifest(
        name=name,
        description=description,
        body="",
        file_path=f"/tmp/{name}/SKILL.md",
        requirements=SkillRequirements(bins=bins or []),
        emoji=emoji,
    )


def test_list_discovered_returns_eligibility() -> None:
    settings = MagicMock()
    manifests = [
        _manifest("tool_a", emoji="🔧"),
        _manifest("tool_missing", bins=["nonexistent_bin_xyz"]),
    ]
    with patch("pyclaw.skills.management.discover_skills", return_value=manifests):
        results = list_discovered(Path("/tmp/ws"), settings)

    by_name = {r.name: r for r in results}
    assert by_name["tool_a"].eligible is True
    assert by_name["tool_a"].emoji == "🔧"
    assert by_name["tool_missing"].eligible is False


def test_check_eligibility_all() -> None:
    settings = MagicMock()
    manifests = [
        _manifest("good"),
        _manifest("bad", bins=["nonexistent_bin_xyz"]),
    ]
    with patch("pyclaw.skills.management.discover_skills", return_value=manifests):
        reports = check_eligibility(Path("/tmp/ws"), settings)

    by_name = {r.name: r for r in reports}
    assert by_name["good"].ok is True
    assert by_name["bad"].ok is False
    assert any("nonexistent_bin_xyz" in issue for issue in by_name["bad"].issues)


def test_check_eligibility_filter_by_name() -> None:
    settings = MagicMock()
    manifests = [_manifest("good"), _manifest("other")]
    with patch("pyclaw.skills.management.discover_skills", return_value=manifests):
        reports = check_eligibility(Path("/tmp/ws"), settings, name="good")

    assert len(reports) == 1
    assert reports[0].name == "good"


@pytest.mark.asyncio
async def test_search_hub_maps_results() -> None:
    hit1 = MagicMock()
    hit1.slug = "github"
    hit1.version = "1.2.3"
    hit1.description = "GitHub skill"
    hit2 = MagicMock()
    hit2.slug = "slack"
    hit2.version = "0.5.0"
    hit2.description = "Slack integration"

    client = AsyncMock()
    client.search = AsyncMock(return_value=[hit1, hit2])

    results = await search_hub("test", client=client)
    assert len(results) == 2
    assert results[0].slug == "github"
    assert results[0].latest_version == "1.2.3"


@pytest.mark.asyncio
async def test_install_returns_ok_result(tmp_path: Path) -> None:
    client = AsyncMock()

    with patch(
        "pyclaw.skills.management.install_skill",
        new_callable=AsyncMock,
        return_value=tmp_path / "github",
    ):
        result = await install("github", None, tmp_path, client=client)

    assert result.ok is True
    assert "github" in (result.dest or "")
    assert result.error is None


@pytest.mark.asyncio
async def test_install_handles_clawhub_error(tmp_path: Path) -> None:
    from pyclaw.skills.models import ClawHubError

    client = AsyncMock()
    with patch(
        "pyclaw.skills.management.install_skill",
        new_callable=AsyncMock,
        side_effect=ClawHubError("not found", 404),
    ):
        result = await install("github", None, tmp_path, client=client)

    assert result.ok is False
    assert "404" in (result.error or "") or "not found" in (result.error or "")
