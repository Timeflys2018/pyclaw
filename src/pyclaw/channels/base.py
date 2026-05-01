from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pyclaw.models import ImageBlock


@dataclass
class InboundMessage:
    """A normalized inbound message from any channel."""

    session_id: str
    user_message: str
    workspace_id: str
    channel: str
    attachments: list[ImageBlock] = field(default_factory=list)
    raw: dict = field(default_factory=dict)  # type: ignore[type-arg]


@dataclass
class OutboundReply:
    """A reply to be sent back via the originating channel."""

    session_id: str
    text: str
    is_error: bool = False


class ChannelPlugin(Protocol):
    """Protocol for channel plugins."""

    name: str

    async def start(self) -> None:
        """Start the channel (connect, register handlers, etc.)."""
        ...

    async def stop(self) -> None:
        """Stop the channel gracefully."""
        ...
