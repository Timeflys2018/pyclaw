from __future__ import annotations

import json
import textwrap

import pytest

from pyclaw.infra.settings import Settings


def _parse(data: dict) -> Settings:
    return Settings.model_validate(data)


def test_agent_timeouts_readable_from_json() -> None:
    s = _parse({"agent": {"timeouts": {"run_seconds": 60}}})
    assert s.agent.timeouts.run_seconds == 60


def test_agent_retry_readable_from_json() -> None:
    s = _parse({"agent": {"retry": {"planning_only_limit": 3}}})
    assert s.agent.retry.planning_only_limit == 3


def test_agent_compaction_readable_from_json() -> None:
    s = _parse({"agent": {"compaction": {"keep_recent_tokens": 5000}}})
    assert s.agent.compaction.keep_recent_tokens == 5000


def test_agent_tools_readable_from_json() -> None:
    s = _parse({"agent": {"tools": {"max_output_chars": 1000}}})
    assert s.agent.tools.max_output_chars == 1000


def test_feishu_app_id_alias() -> None:
    s = _parse({"channels": {"feishu": {"appId": "x"}}})
    assert s.channels.feishu.app_id == "x"


def test_feishu_session_scope_default() -> None:
    s = _parse({})
    assert s.channels.feishu.session_scope == "chat"


def test_feishu_group_context_default() -> None:
    s = _parse({})
    assert s.channels.feishu.group_context == "recent"


def test_feishu_group_context_size_default() -> None:
    s = _parse({})
    assert s.channels.feishu.group_context_size == 20


def test_workspaces_default() -> None:
    s = _parse({})
    assert s.workspaces.default == "~/.pyclaw/workspaces"


def test_full_pyclaw_json_parses_without_error(tmp_path) -> None:
    example = tmp_path / "pyclaw.example.json"
    data = {
        "server": {"host": "0.0.0.0", "port": 8000},
        "redis": {"host": "localhost", "port": 6379, "password": None,
                  "keyPrefix": "pyclaw:", "transcriptRetentionDays": 7},
        "storage": {"session_backend": "memory", "memory_backend": "sqlite", "lock_backend": "file"},
        "agent": {
            "default_model": "gpt-4o",
            "providers": {"openai": {"apiKey": None, "baseURL": None}},
            "max_iterations": 50,
            "context_window": 128000,
            "timeouts": {"run_seconds": 300.0, "idle_seconds": 60.0,
                         "tool_seconds": 120.0, "compaction_seconds": 900.0},
            "retry": {"planning_only_limit": 1, "reasoning_only_limit": 2,
                      "empty_response_limit": 1, "unknown_tool_threshold": 3},
            "compaction": {"model": None, "threshold": 0.8, "keep_recent_tokens": 20000,
                           "timeout_seconds": 900.0, "truncate_after_compaction": False},
            "tools": {"max_output_chars": 25000},
        },
        "workspaces": {"default": "~/.pyclaw/workspaces"},
        "channels": {
            "feishu": {"enabled": False, "appId": "", "appSecret": "",
                       "sessionScope": "chat", "groupContext": "recent", "groupContextSize": 20},
            "web": {"enabled": False, "authToken": ""},
        },
    }
    s = Settings.model_validate(data)
    assert s.agent.default_model == "gpt-4o"
    assert s.channels.feishu.session_scope == "chat"


def test_provider_settings_prefixes_field() -> None:
    s = _parse({
        "agent": {
            "providers": {
                "openai": {
                    "apiKey": "k",
                    "baseURL": "u",
                    "prefixes": ["openai", "azure_openai", "minimax"],
                }
            }
        }
    })
    assert s.agent.providers["openai"].prefixes == ["openai", "azure_openai", "minimax"]


def test_provider_settings_prefixes_default_empty() -> None:
    s = _parse({"agent": {"providers": {"openai": {"apiKey": "k"}}}})
    assert s.agent.providers["openai"].prefixes == []


def test_provider_settings_litellm_provider_field() -> None:
    s = _parse({
        "agent": {
            "providers": {
                "openai": {"apiKey": "k", "litellmProvider": "openai"}
            }
        }
    })
    assert s.agent.providers["openai"].litellm_provider == "openai"


def test_provider_settings_litellm_provider_default_none() -> None:
    s = _parse({"agent": {"providers": {"openai": {"apiKey": "k"}}}})
    assert s.agent.providers["openai"].litellm_provider is None


def test_provider_settings_litellm_provider_snake_case_also_works() -> None:
    s = _parse({
        "agent": {
            "providers": {
                "openai": {"apiKey": "k", "litellm_provider": "openai"}
            }
        }
    })
    assert s.agent.providers["openai"].litellm_provider == "openai"


def test_agent_default_provider_field() -> None:
    s = _parse({"agent": {"default_provider": "openai"}})
    assert s.agent.default_provider == "openai"


def test_agent_default_provider_default_none() -> None:
    s = _parse({"agent": {}})
    assert s.agent.default_provider is None


def test_agent_unknown_prefix_policy_default_fail() -> None:
    s = _parse({"agent": {}})
    assert s.agent.unknown_prefix_policy == "fail"


def test_agent_unknown_prefix_policy_explicit_default() -> None:
    s = _parse({"agent": {"unknown_prefix_policy": "default"}})
    assert s.agent.unknown_prefix_policy == "default"


def test_agent_unknown_prefix_policy_invalid_rejected() -> None:
    with pytest.raises(Exception):
        _parse({"agent": {"unknown_prefix_policy": "lenient"}})


def test_existing_example_config_still_loads() -> None:
    import pathlib
    example_path = pathlib.Path(__file__).resolve().parents[3] / "configs" / "pyclaw.example.json"
    if not example_path.is_file():
        pytest.skip("configs/pyclaw.example.json not present")
    data = json.loads(example_path.read_text(encoding="utf-8"))
    s = Settings.model_validate(data)
    assert isinstance(s.agent.providers, dict)
    assert s.agent.unknown_prefix_policy == "fail"
    assert s.agent.default_provider is None


class TestModelEntrySchema:
    def test_model_modalities_default_text_only(self) -> None:
        from pyclaw.infra.settings import ModelModalities

        m = ModelModalities()
        assert m.input == {"text"}
        assert m.output == {"text"}

    def test_model_modalities_json_list_to_set(self) -> None:
        from pyclaw.infra.settings import ModelModalities

        m = ModelModalities.model_validate(
            {"input": ["text", "image", "pdf"], "output": ["text"]}
        )
        assert m.input == {"text", "image", "pdf"}
        assert m.output == {"text"}

    def test_model_modalities_case_sensitive(self) -> None:
        from pyclaw.infra.settings import ModelModalities

        m = ModelModalities.model_validate({"input": ["Image"], "output": ["text"]})
        assert "image" not in m.input
        assert "Image" in m.input

    def test_model_entry_default_constructs(self) -> None:
        from pyclaw.infra.settings import ModelEntry

        entry = ModelEntry()
        assert entry.modalities.input == {"text"}
        assert entry.modalities.output == {"text"}

    def test_model_entry_with_modalities(self) -> None:
        from pyclaw.infra.settings import ModelEntry

        entry = ModelEntry.model_validate(
            {"modalities": {"input": ["text", "image"], "output": ["text"]}}
        )
        assert entry.modalities.input == {"text", "image"}

    def test_model_entry_extra_fields_ignored(self) -> None:
        from pyclaw.infra.settings import ModelEntry

        entry = ModelEntry.model_validate(
            {
                "modalities": {"input": ["text", "image"], "output": ["text"]},
                "limit": {"context": 128000, "output": 4096},
                "name": "GPT-5.4",
                "attachment": True,
            }
        )
        assert entry.modalities.input == {"text", "image"}

    def test_provider_settings_models_dict_form(self) -> None:
        s = _parse(
            {
                "agent": {
                    "providers": {
                        "openai": {
                            "apiKey": "k",
                            "models": {
                                "azure_openai/gpt-5.4": {
                                    "modalities": {
                                        "input": ["text", "image"],
                                        "output": ["text"],
                                    }
                                },
                                "azure_openai/gpt-5.3-codex": {
                                    "modalities": {
                                        "input": ["text"],
                                        "output": ["text"],
                                    }
                                },
                            },
                        }
                    }
                }
            }
        )
        ps = s.agent.providers["openai"]
        assert isinstance(ps.models, dict)
        assert "azure_openai/gpt-5.4" in ps.models
        assert "image" in ps.models["azure_openai/gpt-5.4"].modalities.input
        assert "image" not in ps.models["azure_openai/gpt-5.3-codex"].modalities.input

    def test_provider_settings_models_empty_dict_ok(self) -> None:
        s = _parse(
            {"agent": {"providers": {"openai": {"apiKey": "k", "models": {}}}}}
        )
        assert s.agent.providers["openai"].models == {}

    def test_provider_settings_models_list_rejected(self) -> None:
        with pytest.raises(Exception) as excinfo:
            _parse(
                {
                    "agent": {
                        "providers": {
                            "openai": {
                                "apiKey": "k",
                                "models": ["azure_openai/gpt-5.4"],
                            }
                        }
                    }
                }
            )
        msg = str(excinfo.value).lower()
        assert "must be a dict" in msg or "dict" in msg
        assert "modalities" in msg or "model_id" in msg or "pyclaw.example.json" in msg

    def test_provider_settings_models_dict_preserves_order(self) -> None:
        ordered_ids = [
            "azure_openai/gpt-5.4",
            "azure_openai/gpt-5.3-codex",
            "azure_openai/gpt-4o",
        ]
        s = _parse(
            {
                "agent": {
                    "providers": {
                        "openai": {
                            "apiKey": "k",
                            "models": {
                                mid: {"modalities": {"input": ["text"], "output": ["text"]}}
                                for mid in ordered_ids
                            },
                        }
                    }
                }
            }
        )
        assert list(s.agent.providers["openai"].models.keys()) == ordered_ids
