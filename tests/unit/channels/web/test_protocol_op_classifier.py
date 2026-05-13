from __future__ import annotations

from pyclaw.channels.web.message_classifier import (
    PROTOCOL_OP_EXACT_COMMANDS,
    PROTOCOL_OP_PREFIX_COMMANDS,
    classify,
    is_protocol_op,
)


class TestStopExactMatchBackwardCompat:
    def test_stop_exact(self):
        assert is_protocol_op("/stop") is True

    def test_stop_with_surrounding_whitespace(self):
        assert is_protocol_op("  /stop  ") is True

    def test_stop_case_insensitive(self):
        assert is_protocol_op("/STOP") is True

    def test_stopping_does_not_match(self):
        assert is_protocol_op("/stopping") is False

    def test_stop_with_args_does_not_match(self):
        assert is_protocol_op("/stop extra") is False


class TestPrefixCommandsWithAsciiSpace:
    def test_bare_steer(self):
        assert is_protocol_op("/steer") is True

    def test_steer_with_ascii_space(self):
        assert is_protocol_op("/steer hello") is True

    def test_bare_btw(self):
        assert is_protocol_op("/btw") is True

    def test_btw_with_ascii_space(self):
        assert is_protocol_op("/btw what is foo") is True

    def test_steering_committee_negative(self):
        assert is_protocol_op("/steering-committee") is False

    def test_tools_negative(self):
        assert is_protocol_op("/tools") is False

    def test_non_prefix_match_negative(self):
        assert is_protocol_op("hello /steer") is False


class TestPrefixCommandsWithUnicodeWhitespace:
    """Adversarial reviewer Invariant 10: multi-line textarea input via Web.

    Without regex-based whitespace matching, a user pasting:
        /steer
        actually use X
    from a Web textarea (which injects \\n between the command and args)
    would be misclassified as slash_command, defeating the mid-run bypass.
    """

    def test_steer_with_newline(self):
        assert is_protocol_op("/steer\nhello") is True

    def test_steer_with_tab(self):
        assert is_protocol_op("/steer\thello") is True

    def test_steer_with_nbsp(self):
        assert is_protocol_op("/steer\u00a0hello") is True

    def test_btw_with_newline(self):
        assert is_protocol_op("/btw\nwhat") is True

    def test_steer_without_any_separator_negative(self):
        assert is_protocol_op("/steering") is False


class TestClassify:
    def test_stop_classifies_as_protocol_op(self):
        assert classify("/stop") == "protocol_op"

    def test_steer_with_args_classifies_as_protocol_op(self):
        assert classify("/steer hello") == "protocol_op"

    def test_btw_with_newline_args_classifies_as_protocol_op(self):
        assert classify("/btw\nfoo") == "protocol_op"

    def test_tools_classifies_as_slash_command(self):
        assert classify("/tools") == "slash_command"

    def test_plain_text_classifies_as_user_message(self):
        assert classify("hello world") == "user_message"


class TestConstantsRenamed:
    def test_exact_commands_contains_stop(self):
        assert "/stop" in PROTOCOL_OP_EXACT_COMMANDS

    def test_prefix_commands_contains_steer_and_btw(self):
        assert "/steer" in PROTOCOL_OP_PREFIX_COMMANDS
        assert "/btw" in PROTOCOL_OP_PREFIX_COMMANDS
