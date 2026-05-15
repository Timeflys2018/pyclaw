from __future__ import annotations

from pyclaw.core.commands._helpers import parse_idle_duration
from pyclaw.infra.settings import AgentSettings, ModelEntry, ModelModalities, ProviderSettings


def _settings_with_dict_models() -> AgentSettings:
    return AgentSettings(
        providers={
            "openai": ProviderSettings(
                api_key="k",
                base_url="u",
                models={
                    "azure_openai/gpt-5.4": ModelEntry(
                        modalities=ModelModalities(input={"text", "image"}, output={"text"})
                    ),
                    "azure_openai/gpt-5.3-codex": ModelEntry(
                        modalities=ModelModalities(input={"text"}, output={"text"})
                    ),
                },
                prefixes=["azure_openai"],
            ),
            "anthropic": ProviderSettings(
                api_key="k",
                base_url="u",
                models={
                    "anthropic/claude-opus-4-7": ModelEntry(
                        modalities=ModelModalities(input={"text", "image", "pdf"}, output={"text"})
                    ),
                },
                prefixes=["anthropic"],
            ),
        }
    )


class TestListAvailableModels:
    def test_returns_dict_provider_to_model_id_list(self) -> None:
        from pyclaw.core.commands._helpers import list_available_models

        result = list_available_models(_settings_with_dict_models())
        assert isinstance(result, dict)
        assert set(result.keys()) == {"openai", "anthropic"}
        assert isinstance(result["openai"], list)
        assert "azure_openai/gpt-5.4" in result["openai"]
        assert "azure_openai/gpt-5.3-codex" in result["openai"]
        assert "anthropic/claude-opus-4-7" in result["anthropic"]

    def test_empty_models_dict_omitted(self) -> None:
        from pyclaw.core.commands._helpers import list_available_models

        agent = AgentSettings(
            providers={
                "openai": ProviderSettings(
                    api_key="k",
                    base_url="u",
                    models={
                        "azure_openai/gpt-5.4": ModelEntry(
                            modalities=ModelModalities(input={"text"}, output={"text"})
                        ),
                    },
                ),
                "empty_provider": ProviderSettings(api_key="k", base_url="u", models={}),
            }
        )
        result = list_available_models(agent)
        assert "empty_provider" not in result
        assert "openai" in result


class TestListAvailableModelsWithModalities:
    def test_returns_provider_to_pairs_of_id_and_modalities(self) -> None:
        from pyclaw.core.commands._helpers import list_available_models_with_modalities

        result = list_available_models_with_modalities(_settings_with_dict_models())
        assert set(result.keys()) == {"openai", "anthropic"}
        openai_pairs = dict(result["openai"])
        assert "image" in openai_pairs["azure_openai/gpt-5.4"].input
        assert "image" not in openai_pairs["azure_openai/gpt-5.3-codex"].input
        anthropic_pairs = dict(result["anthropic"])
        assert "pdf" in anthropic_pairs["anthropic/claude-opus-4-7"].input

    def test_preserves_dict_insertion_order(self) -> None:
        from pyclaw.core.commands._helpers import list_available_models_with_modalities

        agent = AgentSettings(
            providers={
                "openai": ProviderSettings(
                    api_key="k",
                    base_url="u",
                    models={
                        "azure_openai/gpt-5.4": ModelEntry(),
                        "azure_openai/gpt-5.3-codex": ModelEntry(),
                        "azure_openai/gpt-4o": ModelEntry(),
                    },
                )
            }
        )
        result = list_available_models_with_modalities(agent)
        ids = [mid for mid, _ in result["openai"]]
        assert ids == [
            "azure_openai/gpt-5.4",
            "azure_openai/gpt-5.3-codex",
            "azure_openai/gpt-4o",
        ]


def test_parse_idle_duration_minutes() -> None:
    assert parse_idle_duration("30m") == 30
    assert parse_idle_duration("5min") == 5
    assert parse_idle_duration("60mins") == 60
    assert parse_idle_duration("45minutes") == 45


def test_parse_idle_duration_hours() -> None:
    assert parse_idle_duration("2h") == 120
    assert parse_idle_duration("1hour") == 60
    assert parse_idle_duration("3hours") == 180


def test_parse_idle_duration_off() -> None:
    assert parse_idle_duration("off") == 0
    assert parse_idle_duration("0") == 0
    assert parse_idle_duration("disable") == 0
    assert parse_idle_duration("关闭") == 0


def test_parse_idle_duration_invalid() -> None:
    assert parse_idle_duration("garbage") is None
    assert parse_idle_duration("") is None
    assert parse_idle_duration("abc") is None
    assert parse_idle_duration("1d") is None
