"""Tests for /skills slash command (Phase E)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyclaw.infra.settings import Settings
from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.skills import cmd_skills
from pyclaw.skills.management import (
    DiscoveredSkill,
    EligibilityReport,
    HubSearchResult,
    InstallResult,
)


def _ctx(*, reply=None, channel="web", deps=None) -> CommandContext:
    if deps is None:
        deps = MagicMock()
        deps.settings = MagicMock()
    return CommandContext(
        session_id="s1",
        session_key="web:user_x",
        workspace_id="ws",
        user_id="user_x",
        channel=channel,
        deps=deps,
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        reply=reply or AsyncMock(),
        dispatch_user_message=AsyncMock(),
        raw={},
        settings=Settings(),
    )


@pytest.mark.asyncio
async def test_skills_usage_without_args() -> None:
    reply = AsyncMock()
    await cmd_skills("", _ctx(reply=reply))
    assert "用法" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_skills_list_empty() -> None:
    reply = AsyncMock()
    with patch("pyclaw.core.commands.skills.list_discovered", return_value=[]):
        await cmd_skills("list", _ctx(reply=reply))
    assert "未发现" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_skills_list_with_results() -> None:
    reply = AsyncMock()
    skills = [
        DiscoveredSkill(
            name="github", emoji="🐙", description="GitHub CLI",
            eligible=True, location="/tmp/github/SKILL.md",
        ),
        DiscoveredSkill(
            name="broken", emoji=None, description="broken tool",
            eligible=False, location="/tmp/broken/SKILL.md",
        ),
    ]
    with patch("pyclaw.core.commands.skills.list_discovered", return_value=skills):
        await cmd_skills("list", _ctx(reply=reply))

    msg = reply.await_args[0][0]
    assert "github" in msg
    assert "Eligible" in msg
    assert "Ineligible" in msg
    assert "broken" in msg


@pytest.mark.asyncio
async def test_skills_search_empty_query() -> None:
    reply = AsyncMock()
    await cmd_skills("search", _ctx(reply=reply))
    assert "用法" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_skills_search_returns_results() -> None:
    reply = AsyncMock()
    results = [
        HubSearchResult(slug="github", latest_version="1.2.3", description="GitHub skill"),
    ]
    with patch(
        "pyclaw.core.commands.skills.search_hub",
        new_callable=AsyncMock,
        return_value=results,
    ):
        await cmd_skills("search github", _ctx(reply=reply))

    msg = reply.await_args[0][0]
    assert "github" in msg
    assert "1.2.3" in msg


@pytest.mark.asyncio
async def test_skills_search_no_results() -> None:
    reply = AsyncMock()
    with patch(
        "pyclaw.core.commands.skills.search_hub",
        new_callable=AsyncMock,
        return_value=[],
    ):
        await cmd_skills("search nothing", _ctx(reply=reply))
    assert "未找到" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_skills_install_requires_slug() -> None:
    reply = AsyncMock()
    await cmd_skills("install", _ctx(reply=reply))
    assert "用法" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_skills_install_preview_without_confirm() -> None:
    reply = AsyncMock()
    await cmd_skills("install github", _ctx(reply=reply))
    msg = reply.await_args[0][0]
    assert "将安装" in msg
    assert "github" in msg
    assert "--confirm" in msg


@pytest.mark.asyncio
async def test_skills_install_with_version_preview() -> None:
    reply = AsyncMock()
    await cmd_skills("install github --version 2.0.0", _ctx(reply=reply))
    msg = reply.await_args[0][0]
    assert "2.0.0" in msg


@pytest.mark.asyncio
async def test_skills_install_confirmed(tmp_path: Path) -> None:
    reply = AsyncMock()
    ctx = _ctx(reply=reply)
    ctx.workspace_base = tmp_path

    with patch(
        "pyclaw.core.commands.skills.install",
        new_callable=AsyncMock,
        return_value=InstallResult(ok=True, dest=str(tmp_path / "github"), error=None),
    ):
        await cmd_skills("install github --confirm", ctx)
    assert "已安装" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_skills_install_failed(tmp_path: Path) -> None:
    reply = AsyncMock()
    ctx = _ctx(reply=reply)
    ctx.workspace_base = tmp_path

    with patch(
        "pyclaw.core.commands.skills.install",
        new_callable=AsyncMock,
        return_value=InstallResult(ok=False, dest=None, error="network error"),
    ):
        await cmd_skills("install github --confirm", ctx)
    assert "安装失败" in reply.await_args[0][0]
    assert "network error" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_skills_check_empty() -> None:
    reply = AsyncMock()
    with patch("pyclaw.core.commands.skills.check_eligibility", return_value=[]):
        await cmd_skills("check", _ctx(reply=reply))
    assert "未发现" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_skills_check_name_not_found() -> None:
    reply = AsyncMock()
    with patch("pyclaw.core.commands.skills.check_eligibility", return_value=[]):
        await cmd_skills("check nonexistent", _ctx(reply=reply))
    assert "未发现" in reply.await_args[0][0]


@pytest.mark.asyncio
async def test_skills_check_with_reports() -> None:
    reply = AsyncMock()
    reports = [
        EligibilityReport(name="good", ok=True, issues=[]),
        EligibilityReport(name="bad", ok=False, issues=["missing bin: foo"]),
    ]
    with patch("pyclaw.core.commands.skills.check_eligibility", return_value=reports):
        await cmd_skills("check", _ctx(reply=reply))

    msg = reply.await_args[0][0]
    assert "good" in msg
    assert "bad" in msg
    assert "missing bin" in msg


@pytest.mark.asyncio
async def test_skills_unknown_subcommand() -> None:
    reply = AsyncMock()
    await cmd_skills("invalid", _ctx(reply=reply))
    assert "未知子命令" in reply.await_args[0][0]
