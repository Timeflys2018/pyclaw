"""Tests for AgentRunnerDeps.lock_manager field + factory forwarding (Phase D2)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pyclaw.core.agent.factory import create_agent_runner_deps
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.infra.settings import Settings
from pyclaw.storage.session.base import InMemorySessionStore


class TestAgentRunnerDepsLockManager:
    def test_default_is_none(self) -> None:
        deps = AgentRunnerDeps(llm=MagicMock(), tools=MagicMock())
        assert deps.lock_manager is None

    def test_can_assign_lock_manager(self) -> None:
        mock_lock = MagicMock()
        deps = AgentRunnerDeps(llm=MagicMock(), tools=MagicMock(), lock_manager=mock_lock)
        assert deps.lock_manager is mock_lock


class TestFactoryForwardsLockManager:
    @pytest.mark.asyncio
    async def test_factory_accepts_and_forwards_lock_manager(self) -> None:
        mock_lock = MagicMock()
        settings = Settings()

        deps = await create_agent_runner_deps(
            settings,
            InMemorySessionStore(),
            lock_manager=mock_lock,
        )

        assert deps.lock_manager is mock_lock

    @pytest.mark.asyncio
    async def test_factory_default_lock_manager_is_none(self) -> None:
        settings = Settings()
        deps = await create_agent_runner_deps(settings, InMemorySessionStore())
        assert deps.lock_manager is None
