from __future__ import annotations

from pyclaw.core.agent.incomplete_turn import (
    classify_turn,
    extract_thinking,
    retry_message_for,
    strip_thinking,
)


class TestClassifyTurn:
    def test_tool_calls_means_ok(self) -> None:
        assert classify_turn(text="", tool_calls=[{"id": "x"}]) == "ok"

    def test_empty_response(self) -> None:
        assert classify_turn(text="", tool_calls=None) == "empty"

    def test_whitespace_only_is_empty(self) -> None:
        assert classify_turn(text="   \n\t ", tool_calls=None) == "empty"

    def test_reasoning_only_with_tag(self) -> None:
        text = "<thinking>computing answer</thinking>"
        assert classify_turn(text=text, tool_calls=None) == "reasoning"

    def test_reasoning_only_with_explicit_param(self) -> None:
        assert classify_turn(text="", tool_calls=None, reasoning="step by step...") == "reasoning"

    def test_planning_phrase_short(self) -> None:
        text = "I'll read the config and update it."
        assert classify_turn(text=text, tool_calls=None) == "planning"

    def test_planning_numbered_list(self) -> None:
        text = "Let me do this:\n1. Read the config\n2. Update the value\n3. Save"
        assert classify_turn(text=text, tool_calls=None) == "planning"

    def test_normal_answer_is_ok(self) -> None:
        text = "The current value of pi is approximately 3.14159."
        assert classify_turn(text=text, tool_calls=None) == "ok"

    def test_reasoning_plus_visible_is_ok(self) -> None:
        text = "<thinking>pondering</thinking>The answer is 42."
        assert classify_turn(text=text, tool_calls=None) == "ok"


class TestRetryMessages:
    def test_planning_message(self) -> None:
        msg = retry_message_for("planning")
        assert msg is not None and "plan" in msg.lower()

    def test_reasoning_message(self) -> None:
        msg = retry_message_for("reasoning")
        assert msg is not None and "visible" in msg.lower()

    def test_empty_message(self) -> None:
        msg = retry_message_for("empty")
        assert msg is not None and "answer" in msg.lower()

    def test_ok_has_no_retry_message(self) -> None:
        assert retry_message_for("ok") is None


class TestThinkingHelpers:
    def test_strip_removes_thinking_tags(self) -> None:
        assert strip_thinking("<thinking>X</thinking>Y") == "Y"

    def test_extract_returns_tag_contents(self) -> None:
        text = "<thinking>A</thinking> mid <thinking>B</thinking>"
        extracted = extract_thinking(text)
        assert "<thinking>A</thinking>" in extracted
        assert "<thinking>B</thinking>" in extracted
