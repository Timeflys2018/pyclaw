from __future__ import annotations

import pytest

from pyclaw.core.agent.factory import create_agent_runner_deps
from pyclaw.core.agent.llm import LLMClient
from pyclaw.core.agent.runner import AgentRunnerDeps
from pyclaw.infra.settings import ProviderSettings, Settings
from pyclaw.models import TimeoutConfig
from pyclaw.storage.session.base import InMemorySessionStore


def _settings_with_providers(providers: dict[str, ProviderSettings], **agent_overrides) -> Settings:
    s = Settings()
    s.agent.providers = providers
    for k, v in agent_overrides.items():
        setattr(s.agent, k, v)
    return s


async def test_factory_uses_agent_settings_timeouts() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    settings.agent.timeouts = TimeoutConfig(run_seconds=42.0)
    deps = await create_agent_runner_deps(settings, store)
    assert deps.config.timeouts.run_seconds == 42.0


async def test_factory_returns_agent_runner_deps() -> None:
    settings = Settings()
    store = InMemorySessionStore()
    deps = await create_agent_runner_deps(settings, store)
    assert isinstance(deps, AgentRunnerDeps)


async def test_factory_passes_full_providers_dict_to_llm_client() -> None:
    settings = _settings_with_providers(
        {
            "anthropic": ProviderSettings(api_key="ak", base_url="ab", prefixes=["anthropic"]),
            "openai": ProviderSettings(api_key="ok", base_url="ob", prefixes=["openai", "azure_openai"]),
        },
        default_model="anthropic/foo",
    )
    deps = await create_agent_runner_deps(settings, InMemorySessionStore())
    assert isinstance(deps.llm, LLMClient)
    assert set(deps.llm._providers.keys()) == {"anthropic", "openai"}
    assert deps.llm._providers["anthropic"].api_key == "ak"
    assert deps.llm._providers["openai"].api_key == "ok"


async def test_factory_single_provider_routes_via_layer4_catch_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings_with_providers(
        {"openai": ProviderSettings(api_key="k", base_url="u")},
        default_model="gpt-4o",
    )
    deps = await create_agent_runner_deps(settings, InMemorySessionStore())

    captured: dict[str, object] = {}

    async def fake_acompletion(**kwargs):
        captured.update(kwargs)
        async def _empty():
            if False:
                yield None
        return _empty()

    monkeypatch.setattr("litellm.acompletion", fake_acompletion)
    async for _ in deps.llm.stream(messages=[{"role": "user", "content": "x"}], model="gpt-4o"):
        pass
    assert captured["api_key"] == "k"
    assert captured["api_base"] == "u"


async def test_factory_startup_fails_fast_when_default_model_unrouteable() -> None:
    settings = _settings_with_providers(
        {
            "anthropic": ProviderSettings(api_key="ak", base_url="ab", prefixes=["anthropic"]),
            "openai": ProviderSettings(api_key="ok", base_url="ob", prefixes=["openai"]),
        },
        default_model="totally-fake-prefix/foo",
    )
    with pytest.raises(RuntimeError) as exc_info:
        await create_agent_runner_deps(settings, InMemorySessionStore())
    assert "totally-fake-prefix/foo" in str(exc_info.value)
    assert "configs/pyclaw.json" in str(exc_info.value)


async def test_factory_startup_validation_skipped_when_providers_empty() -> None:
    settings = Settings()
    deps = await create_agent_runner_deps(settings, InMemorySessionStore())
    assert isinstance(deps, AgentRunnerDeps)


def test_legacy_llm_client_construction_bypasses_startup_validation() -> None:
    client = LLMClient(default_model="legacy-fake-model", api_key="k", api_base="u")
    assert client.default_model == "legacy-fake-model"
    assert client._providers == {}
    assert client._fallback_key == "k"
