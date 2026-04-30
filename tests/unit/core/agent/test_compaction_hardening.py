from __future__ import annotations

import asyncio

import pytest

from pyclaw.core.agent.compaction_checkpoint import take_checkpoint
from pyclaw.core.agent.compaction_hardening import (
    HARDENED_SUMMARIZER_SYSTEM_PROMPT,
    IDENTIFIER_PRESERVATION_INSTRUCTIONS,
    filter_oversized_messages,
    has_real_conversation,
    sanity_check_token_estimate,
    split_into_chunks,
    strip_tool_result_details,
    summarize_in_stages,
)
from pyclaw.models import MessageEntry, SessionHeader, SessionTree


def test_identifier_preservation_instructions_in_prompt() -> None:
    assert IDENTIFIER_PRESERVATION_INSTRUCTIONS in HARDENED_SUMMARIZER_SYSTEM_PROMPT
    assert "UUID" in IDENTIFIER_PRESERVATION_INSTRUCTIONS
    assert "hostname" in IDENTIFIER_PRESERVATION_INSTRUCTIONS.lower()


class TestHasRealConversation:
    def test_empty_is_false(self) -> None:
        assert has_real_conversation([]) is False

    def test_heartbeat_only_is_false(self) -> None:
        msgs = [
            {"role": "user", "content": "[heartbeat] ping"},
            {"role": "assistant", "content": "[heartbeat] pong"},
        ]
        assert has_real_conversation(msgs) is False

    def test_user_message_is_true(self) -> None:
        msgs = [{"role": "user", "content": "Hello there"}]
        assert has_real_conversation(msgs) is True

    def test_tool_call_counts_as_conversation(self) -> None:
        msgs = [{"role": "assistant", "content": "", "tool_calls": [{"id": "x"}]}]
        assert has_real_conversation(msgs) is True


class TestStripToolResultDetails:
    def test_removes_details_from_tool_content_dicts(self) -> None:
        msgs = [
            {
                "role": "tool",
                "content": [{"type": "text", "text": "result", "details": {"hidden": "metadata"}}],
            }
        ]
        cleaned = strip_tool_result_details(msgs)
        assert "details" not in cleaned[0]["content"][0]
        assert cleaned[0]["content"][0]["text"] == "result"

    def test_removes_top_level_details(self) -> None:
        msgs = [{"role": "tool", "content": "out", "details": {"x": 1}}]
        cleaned = strip_tool_result_details(msgs)
        assert "details" not in cleaned[0]

    def test_passthrough_when_no_details(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        assert strip_tool_result_details(msgs) == msgs


class TestFilterOversizedMessages:
    def test_small_messages_passthrough(self) -> None:
        msgs = [{"role": "user", "content": "tiny"}]
        assert filter_oversized_messages(msgs, context_window=1000) == msgs

    def test_oversized_message_replaced_with_marker(self) -> None:
        giant = "x" * 10_000
        msgs = [{"role": "user", "content": giant}]
        out = filter_oversized_messages(msgs, context_window=1000, oversized_fraction=0.5)
        assert out[0]["content"].startswith("[omitted oversized message from user")


class TestSanityCheckTokenEstimate:
    def test_smaller_after_returned(self) -> None:
        assert sanity_check_token_estimate(100, 50) == 50

    def test_larger_after_becomes_none(self) -> None:
        assert sanity_check_token_estimate(100, 150) is None

    def test_none_after_stays_none(self) -> None:
        assert sanity_check_token_estimate(100, None) is None


class TestSplitIntoChunks:
    def test_single_chunk_for_small_messages(self) -> None:
        msgs = [{"role": "user", "content": "short"}]
        chunks = split_into_chunks(msgs, chunk_token_budget=1000)
        assert len(chunks) == 1

    def test_splits_when_budget_exceeded(self) -> None:
        msgs = [{"role": "user", "content": "x" * 8000} for _ in range(3)]
        chunks = split_into_chunks(msgs, chunk_token_budget=500)
        assert len(chunks) >= 2


@pytest.mark.asyncio
class TestSummarizeInStages:
    async def test_single_stage_for_small_input(self) -> None:
        calls = []

        async def _sum(payload):
            calls.append(payload)
            return "summary"

        msgs = [{"role": "user", "content": "hello"}]
        result = await summarize_in_stages(msgs, summarizer=_sum, chunk_token_budget=10_000)
        assert result == "summary"
        assert len(calls) == 1

    async def test_multi_stage_merges(self) -> None:
        call_count = [0]

        async def _sum(payload):
            call_count[0] += 1
            if call_count[0] == 1 and any(
                "[summary of chunk" in (m.get("content") or "")
                for m in payload
                if isinstance(m.get("content"), str)
            ):
                return "merged"
            return f"chunk-summary-{call_count[0]}"

        msgs = [{"role": "user", "content": "x" * 5000} for _ in range(4)]
        result = await summarize_in_stages(msgs, summarizer=_sum, chunk_token_budget=500)
        assert call_count[0] > 1
        assert "chunk-summary" in result or result == "merged"


class TestCheckpointRollback:
    def test_restores_tree_state(self) -> None:
        header = SessionHeader(id="s1", workspace_id="default", agent_id="main")
        tree = SessionTree(header=header)
        e1 = MessageEntry(id="e1", parent_id=None, role="user", content="first")
        tree.append(e1)

        checkpoint = take_checkpoint(tree)

        e2 = MessageEntry(id="e2", parent_id="e1", role="assistant", content="second")
        tree.append(e2)
        assert tree.leaf_id == "e2"

        checkpoint.restore_into(tree)
        assert tree.leaf_id == "e1"
        assert "e2" not in tree.entries
        assert tree.order == ["e1"]


@pytest.mark.asyncio
async def test_summarize_in_stages_handles_asyncio_gather_like_calls() -> None:
    async def _sum(payload):
        await asyncio.sleep(0)
        return "ok"

    msgs = [{"role": "user", "content": "x"}]
    result = await summarize_in_stages(msgs, summarizer=_sum, chunk_token_budget=100)
    assert result == "ok"
