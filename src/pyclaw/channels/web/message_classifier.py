from __future__ import annotations

from typing import Literal

MessageKind = Literal["protocol_op", "slash_command", "user_message"]

PROTOCOL_OP_TEXT_COMMANDS: frozenset[str] = frozenset({"/stop"})


def is_protocol_op(content: str) -> bool:
    return content.strip().lower() in PROTOCOL_OP_TEXT_COMMANDS


def is_slash_command(content: str) -> bool:
    return content.strip().startswith("/")


def classify(content: str) -> MessageKind:
    if is_protocol_op(content):
        return "protocol_op"
    if is_slash_command(content):
        return "slash_command"
    return "user_message"
