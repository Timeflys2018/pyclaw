from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyclaw.core.commands.context import CommandContext

ALL_CHANNELS: frozenset[str] = frozenset({"feishu", "web"})

CommandHandler = Callable[[str, "CommandContext"], Awaitable[None]]


@dataclass
class CommandSpec:
    name: str
    handler: CommandHandler
    category: str
    help_text: str
    channels: frozenset[str] = field(default_factory=lambda: ALL_CHANNELS)
    aliases: list[str] = field(default_factory=list)
    args_hint: str = ""
    requires_idle: bool = False
