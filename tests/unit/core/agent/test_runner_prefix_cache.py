from __future__ import annotations

import pytest

from pyclaw.core.agent.runner import (
    _MIN_CACHE_TOKENS,
    _build_effective_system,
    _is_anthropic_model,
)


class TestIsAnthropicModel:
    @pytest.mark.parametrize(
        "model",
        [
            "anthropic/claude-sonnet-4-20250514",
            "anthropic/claude-3-opus",
            "claude-3-5-sonnet-20241022",
            "claude-instant-1.2",
            "bedrock/anthropic.claude-3-sonnet-20240229-v1:0",
            "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
            "vertex_ai/claude-sonnet-4@20250514",
            "vertex_ai/claude-3-5-sonnet-v2@20241022",
            "ANTHROPIC/claude-3",
            "Claude-3-Opus",
        ],
    )
    def test_anthropic_models_match(self, model: str) -> None:
        assert _is_anthropic_model(model) is True

    @pytest.mark.parametrize(
        "model",
        [
            "gpt-4o",
            "gpt-4o-mini",
            "openai/gpt-4o",
            "deepseek/deepseek-chat",
            "bedrock/amazon.titan-text-express-v1",
            "vertex_ai/gemini-1.5-pro",
            "ollama/llama3",
            "",
        ],
    )
    def test_non_anthropic_models_no_match(self, model: str) -> None:
        assert _is_anthropic_model(model) is False


class TestBuildEffectiveSystem:
    def _long_text(self, tokens: int = 2000) -> str:
        return "A" * (tokens * 4)

    def test_anthropic_with_sufficient_frozen_returns_blocks(self) -> None:
        frozen = self._long_text(2000)
        result = _build_effective_system(
            frozen_text=frozen,
            other_parts=["per-turn data", "memory ctx"],
            model="anthropic/claude-sonnet-4-20250514",
        )
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0]["type"] == "text"
        assert result[0]["text"] == frozen
        assert result[0]["cache_control"] == {"type": "ephemeral"}
        assert result[1]["type"] == "text"
        assert "cache_control" not in result[1]
        assert "per-turn data" in result[1]["text"]
        assert "memory ctx" in result[1]["text"]

    def test_bedrock_anthropic_returns_blocks(self) -> None:
        frozen = self._long_text(2000)
        result = _build_effective_system(
            frozen_text=frozen,
            other_parts=["dyn"],
            model="bedrock/anthropic.claude-3-sonnet-20240229-v1:0",
        )
        assert isinstance(result, list)
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_vertex_anthropic_returns_blocks(self) -> None:
        frozen = self._long_text(2000)
        result = _build_effective_system(
            frozen_text=frozen,
            other_parts=["dyn"],
            model="vertex_ai/claude-sonnet-4@20250514",
        )
        assert isinstance(result, list)
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_non_anthropic_returns_string(self) -> None:
        frozen = self._long_text(2000)
        result = _build_effective_system(
            frozen_text=frozen,
            other_parts=["per-turn", "memory"],
            model="gpt-4o",
        )
        assert isinstance(result, str)
        assert frozen in result
        assert "per-turn" in result
        assert "memory" in result

    def test_anthropic_with_insufficient_frozen_returns_string(self) -> None:
        short_frozen = "A" * (_MIN_CACHE_TOKENS * 2)
        assert len(short_frozen) // 4 < _MIN_CACHE_TOKENS
        result = _build_effective_system(
            frozen_text=short_frozen,
            other_parts=["dyn"],
            model="anthropic/claude-sonnet-4",
        )
        assert isinstance(result, str)
        assert short_frozen in result

    def test_anthropic_with_empty_rest_returns_single_block(self) -> None:
        frozen = self._long_text(2000)
        result = _build_effective_system(
            frozen_text=frozen,
            other_parts=[],
            model="claude-3-5-sonnet-20241022",
        )
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["text"] == frozen
        assert result[0]["cache_control"] == {"type": "ephemeral"}

    def test_anthropic_with_only_empty_parts_returns_single_block(self) -> None:
        frozen = self._long_text(2000)
        result = _build_effective_system(
            frozen_text=frozen,
            other_parts=["", "", ""],
            model="anthropic/claude-3-opus",
        )
        assert isinstance(result, list)
        assert len(result) == 1

    def test_non_anthropic_empty_rest(self) -> None:
        frozen = "frozen content"
        result = _build_effective_system(
            frozen_text=frozen,
            other_parts=[],
            model="gpt-4o",
        )
        assert result == frozen
