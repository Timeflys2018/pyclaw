from __future__ import annotations

import pytest

from pyclaw.channels.web.message_classifier import classify, is_protocol_op, is_slash_command


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("/stop", "protocol_op"),
        ("/STOP", "protocol_op"),
        ("/Stop", "protocol_op"),
        ("  /stop  ", "protocol_op"),
        ("\n/stop\n", "protocol_op"),
        ("/new", "slash_command"),
        ("/new initial message", "slash_command"),
        ("/help", "slash_command"),
        ("/extract", "slash_command"),
        ("hello world", "user_message"),
        ("", "user_message"),
        ("/", "slash_command"),
        ("not a slash", "user_message"),
        ("/stop something", "slash_command"),
    ],
)
def test_classify(content: str, expected: str) -> None:
    assert classify(content) == expected


def test_is_protocol_op_matches_only_exact_stop() -> None:
    assert is_protocol_op("/stop")
    assert is_protocol_op(" /stop ")
    assert is_protocol_op("/STOP")
    assert not is_protocol_op("/stop now")
    assert not is_protocol_op("/new")


def test_is_slash_command_recognizes_leading_slash() -> None:
    assert is_slash_command("/foo")
    assert is_slash_command("  /foo")
    assert not is_slash_command("foo")
    assert not is_slash_command("")
