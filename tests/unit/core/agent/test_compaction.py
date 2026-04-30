from __future__ import annotations

from pyclaw.core.agent.compaction import (
    build_summarizer_payload,
    estimate_message_tokens,
    estimate_tokens,
    find_cut_point,
    plan_compaction,
    should_compact,
)


class TestTokenEstimation:
    def test_empty_returns_zero(self) -> None:
        assert estimate_tokens("") == 0

    def test_roughly_chars_over_four(self) -> None:
        assert estimate_tokens("a" * 400) == 100

    def test_message_token_includes_content_and_tool_calls(self) -> None:
        msg = {
            "role": "assistant",
            "content": "a" * 400,
            "tool_calls": [
                {
                    "id": "c1",
                    "function": {"name": "read", "arguments": '{"path": "x"}'},
                }
            ],
        }
        tokens = estimate_message_tokens(msg)
        assert tokens > 100


class TestShouldCompact:
    def test_below_threshold_false(self) -> None:
        msgs = [{"role": "user", "content": "short"}]
        assert should_compact(msgs, context_window=100_000) is False

    def test_above_threshold_true(self) -> None:
        large = "x" * 500_000
        msgs = [{"role": "user", "content": large}]
        assert should_compact(msgs, context_window=100_000) is True


class TestFindCutPoint:
    def test_single_message_no_cut(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        assert find_cut_point(msgs) is None

    def test_cuts_before_keep_recent(self) -> None:
        big = "x" * 100_000
        msgs = [
            {"role": "user", "content": big},
            {"role": "assistant", "content": big},
            {"role": "user", "content": big},
            {"role": "assistant", "content": big},
        ]
        cut = find_cut_point(msgs, keep_recent_tokens=30_000)
        assert cut is not None
        assert 0 < cut < len(msgs)

    def test_does_not_split_tool_call_and_result(self) -> None:
        big = "x" * 100_000
        msgs = [
            {"role": "user", "content": big},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{}"}}],
            },
            {"role": "tool", "content": big, "tool_call_id": "c1"},
            {"role": "assistant", "content": big},
        ]
        cut = find_cut_point(msgs, keep_recent_tokens=5_000)
        assert cut is not None
        assert msgs[cut].get("role") != "tool"


class TestPlanCompaction:
    def test_within_budget(self) -> None:
        plan = plan_compaction(
            [{"role": "user", "content": "hi"}],
            context_window=100_000,
        )
        assert plan.should_compact is False
        assert plan.reason == "within-budget"

    def test_needs_compaction(self) -> None:
        big = "x" * 200_000
        msgs = [
            {"role": "user", "content": big},
            {"role": "assistant", "content": big},
            {"role": "user", "content": big},
        ]
        plan = plan_compaction(msgs, context_window=100_000, keep_recent_tokens=30_000)
        assert plan.should_compact is True
        assert plan.cut_index is not None


class TestSummarizerPayload:
    def test_contains_transcript(self) -> None:
        msgs = [
            {"role": "user", "content": "please read X"},
            {"role": "assistant", "content": "done"},
        ]
        payload = build_summarizer_payload(msgs)
        assert payload[0]["role"] == "system"
        assert "please read X" in payload[1]["content"]
        assert "assistant: done" in payload[1]["content"]

    def test_tool_calls_included(self) -> None:
        msgs = [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "read", "arguments": '{"path": "x"}'}}
                ],
            }
        ]
        payload = build_summarizer_payload(msgs)
        assert "read" in payload[1]["content"]
