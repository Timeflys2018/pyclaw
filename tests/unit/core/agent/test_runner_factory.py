from __future__ import annotations

import pytest

from pyclaw.core.agent.factory import create_agent_runner_deps
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.infra.settings import AgentSettings, Settings
from pyclaw.models import TimeoutConfig
from pyclaw.storage.session.base import InMemorySessionStore


def _settings_with_provider(prefix: str, api_key: str) -> Settings:
    from pyclaw.infra.settings import ProviderSettings
    s = Settings()
    s.agent.providers = {prefix: ProviderSettings(api_key=api_key)}
    return s


def test_factory_selects_anthropic_key_by_prefix() -> None:
    settings = _settings_with_provider("anthropic", "test-key")
    store = InMemorySessionStore()
    deps = create_agent_runner_deps(settings, store)
    assert deps.llm._api_key == "test-key"


def test_factory_no_key_for_unknown_prefix() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    deps = create_agent_runner_deps(settings, store)
    assert deps.llm._api_key is None


def test_factory_uses_agent_settings_timeouts() -> None:
    settings = Settings()
    settings.agent.timeouts = TimeoutConfig(run_seconds=42.0)
    store = InMemorySessionStore()
    deps = create_agent_runner_deps(settings, store)
    assert deps.config.timeouts.run_seconds == 42.0


def test_factory_returns_agent_runner_deps() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    deps = create_agent_runner_deps(settings, store)
    assert isinstance(deps, AgentRunnerDeps)
