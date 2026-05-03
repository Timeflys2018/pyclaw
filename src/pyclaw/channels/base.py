from __future__ import annotations

from dataclasses import dataclass, field

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
