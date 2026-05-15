from __future__ import annotations

import pytest

from pyclaw.core.agent.llm import (
    LLMError,
    LLMErrorCode,
    _extract_usage,
    _prepend_system,
    classify_error,
    finalize_tool_calls,
    merge_tool_call_deltas,
    resolve_provider_for_model,
    session_entries_to_llm_messages,
)
from pyclaw.infra.settings import ProviderSettings


def _ps(
    *,
    api_key: str = "k",
    base_url: str = "u",
    models: list[str] | dict | None = None,
    prefixes: list[str] | None = None,
) -> ProviderSettings:
    if isinstance(models, dict):
        models_dict = models
    elif isinstance(models, list):
        models_dict = {
            mid: {"modalities": {"input": ["text"], "output": ["text"]}} for mid in models
        }
    else:
        models_dict = {}
    return ProviderSettings(
        api_key=api_key,
        base_url=base_url,
        models=models_dict,
        prefixes=list(prefixes or []),
    )


class TestClassifyError:
    def test_context_overflow(self) -> None:
        assert (
            classify_error(Exception("maximum context length exceeded"))
            == LLMErrorCode.CONTEXT_OVERFLOW
        )

    def test_rate_limit(self) -> None:
        assert classify_error(Exception("rate limit reached 429")) == LLMErrorCode.RATE_LIMIT

    def test_auth_error(self) -> None:
        assert classify_error(Exception("Invalid API key")) == LLMErrorCode.AUTH_ERROR

    def test_timeout(self) -> None:
        assert classify_error(Exception("request timed out")) == LLMErrorCode.TIMEOUT

    def test_unknown(self) -> None:
        assert classify_error(Exception("something else")) == LLMErrorCode.UNKNOWN

    def test_llm_error_passthrough_preserves_code(self) -> None:
        exc = LLMError(LLMErrorCode.PROVIDER_NOT_FOUND, "check your api key for provider config")
        assert classify_error(exc) == LLMErrorCode.PROVIDER_NOT_FOUND

    def test_llm_error_passthrough_for_any_code(self) -> None:
        exc = LLMError(LLMErrorCode.AUTH_ERROR, "no auth keyword in message")
        assert classify_error(exc) == LLMErrorCode.AUTH_ERROR


class TestMessageConversion:
    def test_user_message(self) -> None:
        out = session_entries_to_llm_messages([{"role": "user", "content": "hi"}])
        assert out == [{"role": "user", "content": "hi"}]

    def test_assistant_with_tool_calls(self) -> None:
        entries = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "read", "arguments": {}},
                    }
                ],
            }
        ]
        out = session_entries_to_llm_messages(entries)
        assert out[0]["tool_calls"][0]["id"] == "call_1"

    def test_tool_message_has_tool_call_id(self) -> None:
        entries = [{"role": "tool", "content": "result", "tool_call_id": "call_1"}]
        out = session_entries_to_llm_messages(entries)
        assert out[0]["tool_call_id"] == "call_1"


class TestToolCallStreamMerging:
    def test_merge_chunked_arguments(self) -> None:
        buffer: dict[int, dict] = {}
        merge_tool_call_deltas(
            buffer,
            [
                {
                    "index": 0,
                    "id": "call_abc",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"pa'},
                }
            ],
        )
        merge_tool_call_deltas(
            buffer,
            [
                {
                    "index": 0,
                    "id": None,
                    "type": None,
                    "function": {"name": None, "arguments": 'th": "x"}'},
                }
            ],
        )

        finalized = finalize_tool_calls(buffer)
        assert finalized[0]["id"] == "call_abc"
        assert finalized[0]["function"]["name"] == "read"
        assert finalized[0]["function"]["arguments"] == '{"path": "x"}'

    def test_multiple_parallel_tool_calls(self) -> None:
        buffer: dict[int, dict] = {}
        merge_tool_call_deltas(
            buffer,
            [
                {
                    "index": 0,
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "read", "arguments": "{}"},
                },
                {
                    "index": 1,
                    "id": "c2",
                    "type": "function",
                    "function": {"name": "write", "arguments": "{}"},
                },
            ],
        )
        finalized = finalize_tool_calls(buffer)
        assert len(finalized) == 2
        assert finalized[0]["id"] == "c1"
        assert finalized[1]["id"] == "c2"


class TestExtractUsage:
    def test_anthropic_via_prompt_tokens_details(self) -> None:
        raw = {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "total_tokens": 1200,
                "prompt_tokens_details": {
                    "cached_tokens": 800,
                    "cache_creation_tokens": 50,
                },
            }
        }
        usage = _extract_usage(raw)
        assert usage is not None
        assert usage.input_tokens == 1000
        assert usage.output_tokens == 200
        assert usage.cache_read_input_tokens == 800
        assert usage.cache_creation_input_tokens == 50

    def test_openai_style_cached_tokens_only(self) -> None:
        raw = {
            "usage": {
                "prompt_tokens": 500,
                "completion_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 300},
            }
        }
        usage = _extract_usage(raw)
        assert usage is not None
        assert usage.cache_read_input_tokens == 300
        assert usage.cache_creation_input_tokens == 0

    def test_legacy_top_level_cache_fields(self) -> None:
        raw = {
            "usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 200,
                "cache_read_input_tokens": 700,
                "cache_creation_input_tokens": 100,
            }
        }
        usage = _extract_usage(raw)
        assert usage is not None
        assert usage.cache_read_input_tokens == 700
        assert usage.cache_creation_input_tokens == 100

    def test_no_cache_fields_zero(self) -> None:
        raw = {"usage": {"prompt_tokens": 100, "completion_tokens": 50}}
        usage = _extract_usage(raw)
        assert usage is not None
        assert usage.cache_read_input_tokens == 0
        assert usage.cache_creation_input_tokens == 0

    def test_no_usage_returns_none(self) -> None:
        assert _extract_usage({}) is None
        assert _extract_usage({"usage": None}) is None


class TestPrependSystem:
    def test_string_system(self) -> None:
        out = _prepend_system([{"role": "user", "content": "hi"}], "you are helpful")
        assert out[0] == {"role": "system", "content": "you are helpful"}
        assert out[1] == {"role": "user", "content": "hi"}

    def test_list_content_blocks_system(self) -> None:
        blocks = [
            {"type": "text", "text": "frozen", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "dynamic"},
        ]
        out = _prepend_system([{"role": "user", "content": "hi"}], blocks)
        assert out[0]["role"] == "system"
        assert out[0]["content"] == blocks
        assert out[1] == {"role": "user", "content": "hi"}

    def test_none_system_unchanged(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        out = _prepend_system(msgs, None)
        assert out == msgs

    def test_empty_string_system_unchanged(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        out = _prepend_system(msgs, "")
        assert out == msgs


class TestResolveProviderForModel:
    def test_layer1_exact_match_in_models(self) -> None:
        providers = {"anthropic": _ps(models=["anthropic/ppio/pa/claude-sonnet-4-6"])}
        name, ps = resolve_provider_for_model("anthropic/ppio/pa/claude-sonnet-4-6", providers)
        assert name == "anthropic"
        assert ps is providers["anthropic"]

    def test_layer2_prefix_match_with_declared_prefixes(self) -> None:
        providers = {
            "openai": _ps(prefixes=["openai", "azure_openai", "minimax"]),
        }
        name, ps = resolve_provider_for_model("azure_openai/gpt-5.4-pro", providers)
        assert name == "openai"

    def test_layer2_prefix_match_without_subpath(self) -> None:
        providers = {"anthropic": _ps(prefixes=["anthropic"])}
        name, _ = resolve_provider_for_model("anthropic", providers)
        assert name == "anthropic"

    def test_layer2_default_prefixes_to_provider_name(self) -> None:
        providers = {"openai": _ps(prefixes=[])}
        name, _ = resolve_provider_for_model("openai/gpt-4o", providers)
        assert name == "openai"

    def test_layer3_first_segment_fallback(self) -> None:
        providers = {
            "anthropic": _ps(),
            "gemini": _ps(),
        }
        name, _ = resolve_provider_for_model("gemini/gemini-2.5-pro", providers)
        assert name == "gemini"

    def test_layer4_single_provider_catch_all(self) -> None:
        providers = {"openai": _ps()}
        name, _ = resolve_provider_for_model("anthropic/claude-3-5-sonnet", providers)
        assert name == "openai"

    def test_layer5_unknown_prefix_policy_default(self) -> None:
        providers = {"anthropic": _ps(), "openai": _ps()}
        name, _ = resolve_provider_for_model(
            "totally-fake-model",
            providers,
            default_provider="openai",
            unknown_prefix_policy="default",
        )
        assert name == "openai"

    def test_layer6_fail_when_no_match(self) -> None:
        providers = {"anthropic": _ps(prefixes=["anthropic"]), "openai": _ps(prefixes=["openai"])}
        with pytest.raises(LLMError) as exc_info:
            resolve_provider_for_model("vertex_ai/gemini", providers)
        exc = exc_info.value
        assert exc.code == LLMErrorCode.PROVIDER_NOT_FOUND
        assert "vertex_ai/gemini" in str(exc)
        assert "anthropic" in str(exc) and "openai" in str(exc)
        assert "prefixes" in str(exc) or "unknown_prefix_policy" in str(exc)

    def test_layer1_overrides_layer2(self) -> None:
        providers = {
            "a": _ps(models=["weird-model"]),
            "b": _ps(prefixes=["weird-model"]),
        }
        name, _ = resolve_provider_for_model("weird-model", providers)
        assert name == "a"

    def test_three_segment_model_routes_via_anthropic_prefix(self) -> None:
        providers = {"anthropic": _ps(prefixes=["anthropic"])}
        name, _ = resolve_provider_for_model("anthropic/ppio/pa/claude-sonnet-4-6", providers)
        assert name == "anthropic"

    def test_unknown_prefix_policy_default_without_default_provider_falls_back_to_fail(
        self,
    ) -> None:
        providers = {"anthropic": _ps(), "openai": _ps()}
        with pytest.raises(LLMError) as exc_info:
            resolve_provider_for_model(
                "vertex_ai/foo",
                providers,
                default_provider=None,
                unknown_prefix_policy="default",
            )
        assert exc_info.value.code == LLMErrorCode.PROVIDER_NOT_FOUND


class TestLLMClientMultiProvider:
    """Mock path: patch litellm.acompletion (function-local import in stream)."""

    @staticmethod
    def _make_anthropic_openai_client():
        from pyclaw.core.agent.llm import LLMClient

        providers = {
            "anthropic": _ps(api_key="ak", base_url="ab", prefixes=["anthropic"]),
            "openai": _ps(api_key="ok", base_url="ob", prefixes=["openai", "azure_openai"]),
        }
        return LLMClient(default_model="anthropic/foo", providers=providers)

    @staticmethod
    async def _empty_stream():
        if False:
            yield None

    @pytest.mark.asyncio
    async def test_stream_routes_anthropic_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._make_anthropic_openai_client()
        async for _ in client.stream(
            messages=[{"role": "user", "content": "hi"}],
            model="anthropic/ppio/pa/claude-sonnet-4-6",
        ):
            pass
        assert captured["api_key"] == "ak"
        assert captured["api_base"] == "ab"
        assert captured["model"] == "anthropic/ppio/pa/claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_same_client_routes_two_models_to_different_creds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[dict[str, object]] = []

        async def fake_acompletion(**kwargs):
            calls.append(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._make_anthropic_openai_client()
        async for _ in client.stream(
            messages=[{"role": "user", "content": "x"}], model="anthropic/foo"
        ):
            pass
        async for _ in client.stream(
            messages=[{"role": "user", "content": "x"}], model="azure_openai/gpt-5"
        ):
            pass
        assert calls[0]["api_key"] == "ak" and calls[0]["api_base"] == "ab"
        assert calls[1]["api_key"] == "ok" and calls[1]["api_base"] == "ob"

    @pytest.mark.asyncio
    async def test_stream_default_model_routes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._make_anthropic_openai_client()
        async for _ in client.stream(messages=[{"role": "user", "content": "x"}], model=None):
            pass
        assert captured["api_key"] == "ak"
        assert captured["model"] == "anthropic/foo"

    @pytest.mark.asyncio
    async def test_legacy_signature_still_streams(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pyclaw.core.agent.llm import LLMClient

        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = LLMClient(default_model="legacy", api_key="legacy_k", api_base="legacy_b")
        async for _ in client.stream(messages=[{"role": "user", "content": "x"}], model="anything"):
            pass
        assert captured["api_key"] == "legacy_k"
        assert captured["api_base"] == "legacy_b"

    @pytest.mark.asyncio
    async def test_providers_take_precedence_over_legacy_creds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pyclaw.core.agent.llm import LLMClient

        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        providers = {"anthropic": _ps(api_key="new_k", base_url="new_b", prefixes=["anthropic"])}
        client = LLMClient(
            default_model="anthropic/foo",
            api_key="legacy_k",
            api_base="legacy_b",
            providers=providers,
        )
        async for _ in client.stream(
            messages=[{"role": "user", "content": "x"}], model="anthropic/foo"
        ):
            pass
        assert captured["api_key"] == "new_k"
        assert captured["api_base"] == "new_b"

    @pytest.mark.asyncio
    async def test_unknown_prefix_raises_provider_not_found_without_acompletion_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        called: list[bool] = []

        async def fake_acompletion(**kwargs):
            called.append(True)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._make_anthropic_openai_client()
        with pytest.raises(LLMError) as exc_info:
            async for _ in client.stream(
                messages=[{"role": "user", "content": "x"}], model="vertex_ai/gemini"
            ):
                pass
        assert exc_info.value.code == LLMErrorCode.PROVIDER_NOT_FOUND
        assert called == []

    def test_positional_providers_raises_typeerror(self) -> None:
        from pyclaw.core.agent.llm import LLMClient

        with pytest.raises(TypeError):
            LLMClient("m", "k", "b", {"anthropic": _ps()})  # type: ignore[misc]

    @pytest.mark.asyncio
    async def test_complete_path_routes_credentials(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._make_anthropic_openai_client()
        await client.complete(
            messages=[{"role": "user", "content": "x"}],
            model="azure_openai/gpt-5",
        )
        assert captured["api_key"] == "ok"
        assert captured["api_base"] == "ob"

    @pytest.mark.asyncio
    async def test_litellm_provider_injected_when_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pyclaw.core.agent.llm import LLMClient

        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        providers = {
            "openai": _ps(
                api_key="k",
                base_url="u",
                prefixes=["azure_openai"],
            ),
        }
        providers["openai"].litellm_provider = "openai"
        client = LLMClient(default_model="azure_openai/foo", providers=providers)
        async for _ in client.stream(
            messages=[{"role": "user", "content": "x"}], model="azure_openai/gpt-5.4"
        ):
            pass
        assert captured["custom_llm_provider"] == "openai"
        assert captured["model"] == "azure_openai/gpt-5.4"

    @pytest.mark.asyncio
    async def test_litellm_provider_falls_back_to_dict_key_when_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._make_anthropic_openai_client()
        async for _ in client.stream(
            messages=[{"role": "user", "content": "x"}], model="anthropic/foo"
        ):
            pass
        assert captured["custom_llm_provider"] == "anthropic"

    @pytest.mark.asyncio
    async def test_d11_dict_key_fallback_when_litellm_provider_unset(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pyclaw.core.agent.llm import LLMClient

        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        providers = {
            "openai": _ps(api_key="k", base_url="u", prefixes=["azure_openai"]),
        }
        assert providers["openai"].litellm_provider is None
        client = LLMClient(default_model="azure_openai/foo", providers=providers)
        async for _ in client.stream(
            messages=[{"role": "user", "content": "x"}], model="azure_openai/gpt-5.4"
        ):
            pass
        assert captured["custom_llm_provider"] == "openai"

    @pytest.mark.asyncio
    async def test_d11_explicit_litellm_provider_overrides_dict_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pyclaw.core.agent.llm import LLMClient

        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        ps = _ps(api_key="k", base_url="u", prefixes=["azure_openai"])
        ps.litellm_provider = "openai"
        client = LLMClient(default_model="azure_openai/foo", providers={"mify_us": ps})
        async for _ in client.stream(
            messages=[{"role": "user", "content": "x"}], model="azure_openai/gpt-5.4"
        ):
            pass
        assert captured["custom_llm_provider"] == "openai"

    @pytest.mark.asyncio
    async def test_d11_legacy_signature_does_not_inject_dict_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pyclaw.core.agent.llm import LLMClient

        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = LLMClient(default_model="legacy", api_key="k", api_base="u")
        async for _ in client.stream(messages=[{"role": "user", "content": "x"}], model="anything"):
            pass
        assert "custom_llm_provider" not in captured

    @pytest.mark.asyncio
    async def test_litellm_provider_switches_per_call(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pyclaw.core.agent.llm import LLMClient

        calls: list[dict[str, object]] = []

        async def fake_acompletion(**kwargs):
            calls.append(kwargs)
            return TestLLMClientMultiProvider._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        anthropic_ps = _ps(api_key="ak", base_url="ab", prefixes=["anthropic"])
        anthropic_ps.litellm_provider = "anthropic"
        openai_ps = _ps(api_key="ok", base_url="ob", prefixes=["azure_openai"])
        openai_ps.litellm_provider = "openai"
        client = LLMClient(
            default_model="anthropic/foo",
            providers={"anthropic": anthropic_ps, "openai": openai_ps},
        )
        async for _ in client.stream(
            messages=[{"role": "user", "content": "x"}], model="anthropic/foo"
        ):
            pass
        async for _ in client.stream(
            messages=[{"role": "user", "content": "x"}], model="azure_openai/bar"
        ):
            pass
        assert calls[0]["custom_llm_provider"] == "anthropic"
        assert calls[1]["custom_llm_provider"] == "openai"


def _ps_with_models(
    models: dict[str, dict] | None = None,
    *,
    api_key: str = "k",
    base_url: str = "u",
    prefixes: list[str] | None = None,
) -> ProviderSettings:
    return ProviderSettings(
        api_key=api_key,
        base_url=base_url,
        models=models or {},
        prefixes=list(prefixes or []),
    )


class TestModelSupportsInput:
    def test_declared_image_input_returns_true(self) -> None:
        from pyclaw.core.agent.llm import model_supports_input

        providers = {
            "openai": _ps_with_models(
                {
                    "azure_openai/gpt-5.4": {
                        "modalities": {"input": ["text", "image"], "output": ["text"]}
                    }
                },
                prefixes=["azure_openai"],
            )
        }
        assert model_supports_input("azure_openai/gpt-5.4", providers, "image") is True

    def test_undeclared_modality_returns_false(self) -> None:
        from pyclaw.core.agent.llm import model_supports_input

        providers = {
            "openai": _ps_with_models(
                {
                    "azure_openai/gpt-5.4": {
                        "modalities": {"input": ["text", "image"], "output": ["text"]}
                    }
                },
                prefixes=["azure_openai"],
            )
        }
        assert model_supports_input("azure_openai/gpt-5.4", providers, "audio") is False

    def test_text_only_model_returns_false_for_image(self) -> None:
        from pyclaw.core.agent.llm import model_supports_input

        providers = {
            "openai": _ps_with_models(
                {
                    "azure_openai/gpt-5.3-codex": {
                        "modalities": {"input": ["text"], "output": ["text"]}
                    }
                },
                prefixes=["azure_openai"],
            )
        }
        assert model_supports_input("azure_openai/gpt-5.3-codex", providers, "image") is False

    def test_model_not_in_models_dict_returns_false(self) -> None:
        from pyclaw.core.agent.llm import model_supports_input

        providers = {"openai": _ps_with_models(prefixes=["azure_openai"])}
        assert model_supports_input("azure_openai/unknown-model", providers, "image") is False

    def test_provider_not_found_re_raises_llmerror(self) -> None:
        from pyclaw.core.agent.llm import model_supports_input

        providers = {
            "openai": _ps_with_models(prefixes=["openai"]),
            "anthropic": _ps_with_models(prefixes=["anthropic"]),
        }
        with pytest.raises(LLMError) as excinfo:
            model_supports_input("gemini/pro", providers, "image")
        assert excinfo.value.code == LLMErrorCode.PROVIDER_NOT_FOUND

    def test_modality_case_sensitive(self) -> None:
        from pyclaw.core.agent.llm import model_supports_input

        providers = {
            "openai": _ps_with_models(
                {
                    "azure_openai/gpt-5.4": {
                        "modalities": {"input": ["text", "image"], "output": ["text"]}
                    }
                },
                prefixes=["azure_openai"],
            )
        }
        assert model_supports_input("azure_openai/gpt-5.4", providers, "Image") is False


class TestMessagesHaveUserImageContent:
    def test_openai_image_url_block_detected(self) -> None:
        from pyclaw.core.agent.llm import messages_have_user_image_content

        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "what's this?"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        assert messages_have_user_image_content(msgs) is True

    def test_anthropic_image_block_detected(self) -> None:
        from pyclaw.core.agent.llm import messages_have_user_image_content

        msgs = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
                    },
                ],
            }
        ]
        assert messages_have_user_image_content(msgs) is True

    def test_text_only_string_returns_false(self) -> None:
        from pyclaw.core.agent.llm import messages_have_user_image_content

        msgs = [{"role": "user", "content": "hello"}]
        assert messages_have_user_image_content(msgs) is False

    def test_text_only_block_list_returns_false(self) -> None:
        from pyclaw.core.agent.llm import messages_have_user_image_content

        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        assert messages_have_user_image_content(msgs) is False

    def test_assistant_image_not_counted(self) -> None:
        from pyclaw.core.agent.llm import messages_have_user_image_content

        msgs = [
            {
                "role": "assistant",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        assert messages_have_user_image_content(msgs) is False

    def test_system_image_not_counted(self) -> None:
        from pyclaw.core.agent.llm import messages_have_user_image_content

        msgs = [
            {
                "role": "system",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        assert messages_have_user_image_content(msgs) is False

    def test_multiple_user_messages_first_match_returns_true(self) -> None:
        from pyclaw.core.agent.llm import messages_have_user_image_content

        msgs = [
            {"role": "user", "content": "first turn"},
            {"role": "assistant", "content": "ok"},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            },
        ]
        assert messages_have_user_image_content(msgs) is True


class TestLLMClientSecondaryVisionPreflight:
    @staticmethod
    async def _empty_stream():
        if False:
            yield None

    @staticmethod
    def _vision_text_client():
        from pyclaw.core.agent.llm import LLMClient

        providers = {
            "openai": _ps_with_models(
                {
                    "azure_openai/gpt-5.4": {
                        "modalities": {"input": ["text", "image"], "output": ["text"]}
                    },
                    "azure_openai/gpt-5.3-codex": {
                        "modalities": {"input": ["text"], "output": ["text"]}
                    },
                },
                api_key="ok",
                base_url="ob",
                prefixes=["azure_openai"],
            ),
        }
        return LLMClient(default_model="azure_openai/gpt-5.4", providers=providers)

    @pytest.mark.asyncio
    async def test_secondary_allows_vision_capable_model_with_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientSecondaryVisionPreflight._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._vision_text_client()
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                    {"type": "text", "text": "what is this"},
                ],
            }
        ]
        async for _ in client.stream(messages=msgs, model="azure_openai/gpt-5.4"):
            pass
        assert captured["model"] == "azure_openai/gpt-5.4"

    @pytest.mark.asyncio
    async def test_secondary_rejects_non_vision_model_with_image(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_acompletion(**kwargs):
            raise AssertionError("acompletion should NOT be called")

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._vision_text_client()
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        with pytest.raises(LLMError) as excinfo:
            async for _ in client.stream(messages=msgs, model="azure_openai/gpt-5.3-codex"):
                pass
        assert excinfo.value.code == LLMErrorCode.VISION_NOT_SUPPORTED
        assert "azure_openai/gpt-5.3-codex" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_secondary_passes_for_text_only_with_non_vision_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientSecondaryVisionPreflight._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._vision_text_client()
        msgs = [{"role": "user", "content": "plain text only"}]
        async for _ in client.stream(messages=msgs, model="azure_openai/gpt-5.3-codex"):
            pass
        assert captured["model"] == "azure_openai/gpt-5.3-codex"

    @pytest.mark.asyncio
    async def test_secondary_complete_path_inherits_check(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_acompletion(**kwargs):
            raise AssertionError("acompletion should NOT be called")

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._vision_text_client()
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        with pytest.raises(LLMError) as excinfo:
            await client.complete(messages=msgs, model="azure_openai/gpt-5.3-codex")
        assert excinfo.value.code == LLMErrorCode.VISION_NOT_SUPPORTED

    @pytest.mark.asyncio
    async def test_secondary_legacy_signature_skipped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from pyclaw.core.agent.llm import LLMClient

        captured: dict[str, object] = {}

        async def fake_acompletion(**kwargs):
            captured.update(kwargs)
            return TestLLMClientSecondaryVisionPreflight._empty_stream()

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = LLMClient(default_model="legacy", api_key="lk", api_base="lb")
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        async for _ in client.stream(messages=msgs, model="anything"):
            pass
        assert captured["model"] == "anything"

    @pytest.mark.asyncio
    async def test_secondary_error_message_lists_vision_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_acompletion(**kwargs):
            raise AssertionError

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._vision_text_client()
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        with pytest.raises(LLMError) as excinfo:
            async for _ in client.stream(messages=msgs, model="azure_openai/gpt-5.3-codex"):
                pass
        msg = str(excinfo.value)
        assert "azure_openai/gpt-5.4" in msg
        assert "openai" in msg

    @pytest.mark.asyncio
    async def test_secondary_outside_try_block_preserves_code(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_acompletion(**kwargs):
            raise AssertionError

        monkeypatch.setattr("litellm.acompletion", fake_acompletion)
        client = self._vision_text_client()
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ],
            }
        ]
        with pytest.raises(LLMError) as excinfo:
            async for _ in client.stream(messages=msgs, model="azure_openai/gpt-5.3-codex"):
                pass
        assert classify_error(excinfo.value) == LLMErrorCode.VISION_NOT_SUPPORTED
