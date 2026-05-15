from __future__ import annotations

import asyncio
import logging

from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.spec import CommandSpec

logger = logging.getLogger(__name__)


class CommandRegistry:
    def __init__(self) -> None:
        self._specs: dict[str, CommandSpec] = {}
        self._primary_names: dict[str, CommandSpec] = {}

    def register(self, spec: CommandSpec) -> None:
        if not asyncio.iscoroutinefunction(spec.handler):
            raise TypeError(
                f"Command {spec.name} handler must be async (got "
                f"{getattr(spec.handler, '__qualname__', repr(spec.handler))})"
            )

        all_names = [spec.name, *spec.aliases]
        for name in all_names:
            if name in self._specs:
                existing = self._specs[name]
                raise ValueError(
                    f"Command {name!r} already registered "
                    f"(existing handler: {existing.handler.__qualname__}, "
                    f"new handler: {spec.handler.__qualname__})"
                )

        self._primary_names[spec.name] = spec
        for name in all_names:
            self._specs[name] = spec

    def get(self, name: str) -> CommandSpec | None:
        return self._specs.get(name)

    def list_all(self) -> list[CommandSpec]:
        return list(self._primary_names.values())

    def list_by_category(self) -> dict[str, list[CommandSpec]]:
        grouped: dict[str, list[CommandSpec]] = {}
        for spec in self._primary_names.values():
            grouped.setdefault(spec.category, []).append(spec)
        for specs in grouped.values():
            specs.sort(key=lambda s: s.name)
        return dict(sorted(grouped.items()))

    async def dispatch(self, name: str, args: str, ctx: CommandContext) -> bool:
        spec = self.get(name)
        if spec is None:
            return False
        if ctx.channel not in spec.channels:
            allowed = sorted(spec.channels)
            await ctx.reply(f"❌ 命令 {name} 仅在 {allowed} 可用")
            return True
        try:
            await asyncio.wait_for(spec.handler(args, ctx), timeout=ctx.command_timeout)
        except TimeoutError:
            await ctx.reply(f"⏱ 命令 {name} 超过 {ctx.command_timeout}s 未完成")
        return True


_default_registry: CommandRegistry | None = None


def get_default_registry() -> CommandRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = CommandRegistry()
    return _default_registry


def reset_default_registry() -> None:
    global _default_registry
    _default_registry = None
