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
