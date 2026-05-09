from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.commands.context import CommandContext
from pyclaw.core.commands.registry import CommandRegistry
from pyclaw.core.commands.spec import ALL_CHANNELS, CommandSpec


def _make_ctx(channel: str = "feishu", **overrides) -> CommandContext:
    reply = AsyncMock(return_value=None)
    dispatch = AsyncMock(return_value=None)
    base = dict(
        session_id="sid",
        session_key="key",
        workspace_id="ws",
        user_id="user",
        channel=channel,
        deps=MagicMock(),
        session_router=MagicMock(),
        workspace_base=Path("/tmp"),
        reply=reply,
        dispatch_user_message=dispatch,
        raw={"channel": channel},
    )
    base.update(overrides)
    return CommandContext(**base)


@pytest.mark.asyncio
async def test_register_and_get() -> None:
    async def handler(args: str, ctx: CommandContext) -> None:
        pass

    registry = CommandRegistry()
    spec = CommandSpec(
        name="/foo", handler=handler, category="session", help_text="foo cmd"
    )
    registry.register(spec)
    assert registry.get("/foo") is spec
    assert registry.get("/missing") is None


@pytest.mark.asyncio
async def test_alias_resolves_to_same_spec() -> None:
    async def handler(args: str, ctx: CommandContext) -> None:
        pass

    registry = CommandRegistry()
    spec = CommandSpec(
        name="/extract",
        handler=handler,
        category="evolution",
        help_text="extract",
        aliases=["/learn"],
    )
    registry.register(spec)
    assert registry.get("/extract") is registry.get("/learn")


@pytest.mark.asyncio
async def test_alias_collision_with_existing_name() -> None:
    async def h1(args: str, ctx: CommandContext) -> None:
        pass

    async def h2(args: str, ctx: CommandContext) -> None:
        pass

    registry = CommandRegistry()
    registry.register(
        CommandSpec(name="/foo", handler=h1, category="c", help_text="t")
    )
    with pytest.raises(ValueError, match="/foo"):
        registry.register(
            CommandSpec(
                name="/bar",
                handler=h2,
                category="c",
                help_text="t",
                aliases=["/foo"],
            )
        )


@pytest.mark.asyncio
async def test_alias_collision_with_existing_alias() -> None:
    async def h1(args: str, ctx: CommandContext) -> None:
        pass

    async def h2(args: str, ctx: CommandContext) -> None:
        pass

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/foo",
            handler=h1,
            category="c",
            help_text="t",
            aliases=["/x"],
        )
    )
    with pytest.raises(ValueError, match="/x"):
        registry.register(
            CommandSpec(
                name="/bar",
                handler=h2,
                category="c",
                help_text="t",
                aliases=["/x"],
            )
        )


@pytest.mark.asyncio
async def test_duplicate_name_raises_value_error() -> None:
    async def h(args: str, ctx: CommandContext) -> None:
        pass

    registry = CommandRegistry()
    registry.register(CommandSpec(name="/foo", handler=h, category="c", help_text="t"))
    with pytest.raises(ValueError, match="/foo"):
        registry.register(CommandSpec(name="/foo", handler=h, category="c", help_text="t"))


def test_sync_handler_raises_type_error() -> None:
    def sync_handler(args: str, ctx) -> None:
        pass

    registry = CommandRegistry()
    with pytest.raises(TypeError, match="must be async"):
        registry.register(
            CommandSpec(
                name="/foo",
                handler=sync_handler,
                category="c",
                help_text="t",
            )
        )


def test_command_spec_defaults() -> None:
    async def h(args: str, ctx) -> None:
        pass

    spec = CommandSpec(name="/x", handler=h, category="c", help_text="t")
    assert spec.channels == ALL_CHANNELS
    assert spec.aliases == []
    assert spec.args_hint == ""


@pytest.mark.asyncio
async def test_list_by_category_returns_grouped() -> None:
    async def h(args: str, ctx) -> None:
        pass

    registry = CommandRegistry()
    registry.register(CommandSpec(name="/a", handler=h, category="g1", help_text="t"))
    registry.register(CommandSpec(name="/b", handler=h, category="g2", help_text="t"))
    registry.register(CommandSpec(name="/c", handler=h, category="g1", help_text="t"))

    grouped = registry.list_by_category()
    assert set(grouped.keys()) == {"g1", "g2"}
    assert [s.name for s in grouped["g1"]] == ["/a", "/c"]
    assert [s.name for s in grouped["g2"]] == ["/b"]


@pytest.mark.asyncio
async def test_dispatch_unknown_returns_false() -> None:
    registry = CommandRegistry()
    ctx = _make_ctx()
    result = await registry.dispatch("/missing", "", ctx)
    assert result is False


@pytest.mark.asyncio
async def test_dispatch_invokes_handler() -> None:
    captured: list[tuple[str, CommandContext]] = []

    async def h(args: str, ctx: CommandContext) -> None:
        captured.append((args, ctx))

    registry = CommandRegistry()
    registry.register(CommandSpec(name="/foo", handler=h, category="c", help_text="t"))
    ctx = _make_ctx()
    result = await registry.dispatch("/foo", "the args", ctx)
    assert result is True
    assert len(captured) == 1
    assert captured[0][0] == "the args"


@pytest.mark.asyncio
async def test_dispatch_channel_restriction_replies_and_returns_true() -> None:
    async def h(args: str, ctx: CommandContext) -> None:
        pass

    registry = CommandRegistry()
    registry.register(
        CommandSpec(
            name="/feishuonly",
            handler=h,
            category="c",
            help_text="t",
            channels=frozenset({"feishu"}),
        )
    )
    ctx = _make_ctx(channel="web")
    result = await registry.dispatch("/feishuonly", "", ctx)
    assert result is True
    ctx.reply.assert_awaited_once()
    msg = ctx.reply.await_args[0][0]
    assert "/feishuonly" in msg
    assert "feishu" in msg


@pytest.mark.asyncio
async def test_reply_callable_works() -> None:
    captured: list[str] = []

    async def reply(text: str) -> None:
        captured.append(text)

    async def h(args: str, ctx: CommandContext) -> None:
        await ctx.reply("hello")

    registry = CommandRegistry()
    registry.register(CommandSpec(name="/foo", handler=h, category="c", help_text="t"))
    ctx = _make_ctx()
    ctx.reply = reply
    await registry.dispatch("/foo", "", ctx)
    assert captured == ["hello"]
