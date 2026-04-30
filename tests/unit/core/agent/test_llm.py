from __future__ import annotations

from pyclaw.core.agent.llm import (
    LLMErrorCode,
    classify_error,
    session_entries_to_llm_messages,
    finalize_tool_calls,
    merge_tool_call_deltas,
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
        assert finalized[0]["function"]["arguments"] == {"path": "x"}

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
