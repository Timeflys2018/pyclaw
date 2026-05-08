from __future__ import annotations

from pyclaw.core.agent.llm import (
    LLMErrorCode,
    classify_error,
    session_entries_to_llm_messages,
    finalize_tool_calls,
    merge_tool_call_deltas,
    _extract_usage,
    _prepend_system,
)


class TestClassifyError:
    def test_context_overflow(self) -> None:
        assert classify_error(Exception("maximum context length exceeded")) == LLMErrorCode.CONTEXT_OVERFLOW

    def test_rate_limit(self) -> None:
        assert classify_error(Exception("rate limit reached 429")) == LLMErrorCode.RATE_LIMIT

    def test_auth_error(self) -> None:
        assert classify_error(Exception("Invalid API key")) == LLMErrorCode.AUTH_ERROR

    def test_timeout(self) -> None:
        assert classify_error(Exception("request timed out")) == LLMErrorCode.TIMEOUT

    def test_unknown(self) -> None:
        assert classify_error(Exception("something else")) == LLMErrorCode.UNKNOWN


class TestMessageConversion:
    def test_user_message(self) -> None:
        out = session_entries_to_llm_messages([{"role": "user", "content": "hi"}])
        assert out == [{"role": "user", "content": "hi"}]

    def test_assistant_with_tool_calls(self) -> None:
        entries = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read", "arguments": {}}}],
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
            [{"index": 0, "id": "call_abc", "type": "function",
              "function": {"name": "read", "arguments": '{"pa'}}],
        )
        merge_tool_call_deltas(
            buffer,
            [{"index": 0, "id": None, "type": None,
              "function": {"name": None, "arguments": 'th": "x"}'}}],
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
                {"index": 0, "id": "c1", "type": "function",
                 "function": {"name": "read", "arguments": "{}"}},
                {"index": 1, "id": "c2", "type": "function",
                 "function": {"name": "write", "arguments": "{}"}},
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
