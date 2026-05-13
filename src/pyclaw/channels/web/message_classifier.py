from __future__ import annotations

import re
from typing import Literal

MessageKind = Literal["protocol_op", "slash_command", "user_message"]

PROTOCOL_OP_EXACT_COMMANDS: frozenset[str] = frozenset({"/stop"})
PROTOCOL_OP_PREFIX_COMMANDS: frozenset[str] = frozenset({"/steer", "/btw"})

PROTOCOL_OP_PREFIX_REGEX = re.compile(
    r"^(" + "|".join(re.escape(p) for p in PROTOCOL_OP_PREFIX_COMMANDS) + r")\s"
)


def is_protocol_op(content: str) -> bool:
    stripped = content.strip().lower()
    if stripped in PROTOCOL_OP_EXACT_COMMANDS:
        return True
    if stripped in PROTOCOL_OP_PREFIX_COMMANDS:
        return True
    return PROTOCOL_OP_PREFIX_REGEX.match(stripped) is not None


def is_slash_command(content: str) -> bool:
    return content.strip().startswith("/")


def classify(content: str) -> MessageKind:
    if is_protocol_op(content):
        return "protocol_op"
    if is_slash_command(content):
        return "slash_command"
    return "user_message"
