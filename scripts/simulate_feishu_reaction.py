from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from pyclaw.channels.feishu.handler import (  # noqa: E402
    _reaction_last_handled,
    handle_feishu_reaction_created,
)


def _build_fake_event(message_id: str, emoji: str, user_open_id: str):
    event = MagicMock()
    event.event = MagicMock()
    event.event.message_id = message_id
    event.event.reaction_type = MagicMock()
    event.event.reaction_type.emoji_type = emoji
    event.event.user_id = MagicMock()
    event.event.user_id.open_id = user_open_id
    return event


def _build_fake_ctx(
    *,
    is_bot_message_return: bool,
    current_session_id: str | None,
    app_id: str,
) -> MagicMock:
    from unittest.mock import AsyncMock

    client = MagicMock()
    client.is_bot_message = MagicMock(return_value=is_bot_message_return)

    store = MagicMock()
    store.get_current_session_id = AsyncMock(return_value=current_session_id)

    router = MagicMock()
    router.store = store
    router.update_last_interaction = AsyncMock()

    queue_registry = MagicMock()

    async def _capture(sid, coro, **_kwargs):
        print(f"  ✓ enqueue CALLED with session_id={sid!r}")
        coro.close()
        _capture.was_called = True

    _capture.was_called = False
    queue_registry.enqueue = AsyncMock(side_effect=_capture)

    settings = MagicMock()
    settings.app_id = app_id

    ctx = MagicMock()
    ctx.feishu_client = client
    ctx.session_router = router
    ctx.queue_registry = queue_registry
    ctx.settings = settings
    ctx.workspace_base = Path("/tmp/fake_ws_base")
    return ctx, _capture


async def _scenario(
    name: str,
    *,
    is_bot_message_return: bool,
    current_session_id: str | None,
    emoji: str,
    user: str,
    app_id: str,
) -> bool:
    print(f"\n═══ Scenario: {name} ═══")
    _reaction_last_handled.clear()
    event = _build_fake_event(f"om_{name}", emoji, user)
    ctx, capture = _build_fake_ctx(
        is_bot_message_return=is_bot_message_return,
        current_session_id=current_session_id,
        app_id=app_id,
    )
    print(
        f"  Input: emoji={emoji!r} user={user!r} "
        f"is_bot_message={is_bot_message_return} "
        f"current_session={current_session_id!r}"
    )
    await handle_feishu_reaction_created(event, ctx)
    result = bool(capture.was_called)
    expected = is_bot_message_return and current_session_id is not None
    verdict = "✅ PASS" if result == expected else "❌ FAIL"
    print(f"  {verdict}: enqueue called = {result} (expected {expected})")
    return result == expected


async def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Simulate Feishu reaction events to drive the handler directly "
            "(no real WebSocket). Use when debugging reaction flow or when "
            "real Feishu E2E fails — this script isolates handler logic from "
            "event-subscription / message-tracking / session-state issues."
        ),
        epilog=(
            "Example: .venv/bin/python scripts/simulate_feishu_reaction.py "
            "--emoji HEART --user ou_abc"
        ),
    )
    parser.add_argument("--app-id", default="cli_test_app", help="Feishu app_id")
    parser.add_argument("--user", default="ou_test_user_1", help="User open_id")
    parser.add_argument("--emoji", default="THUMBSUP", help="Emoji type (e.g. THUMBSUP, HEART)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    print("=" * 60)
    print("PyClaw Feishu Reaction Handler — Simulation Diagnostic")
    print("=" * 60)
    print("Note: this drives the handler directly with fake events;")
    print("it does NOT talk to Feishu servers. Use it to verify")
    print("handler logic. Real Feishu E2E still requires the bot + UI.")

    results = []
    results.append(await _scenario(
        "happy_path",
        is_bot_message_return=True,
        current_session_id=f"feishu:{args.app_id}:{args.user}:s:test1234",
        emoji=args.emoji,
        user=args.user,
        app_id=args.app_id,
    ))
    results.append(await _scenario(
        "not_bot_message",
        is_bot_message_return=False,
        current_session_id=f"feishu:{args.app_id}:{args.user}:s:test1234",
        emoji=args.emoji,
        user=args.user,
        app_id=args.app_id,
    ))
    results.append(await _scenario(
        "no_active_session",
        is_bot_message_return=True,
        current_session_id=None,
        emoji=args.emoji,
        user=args.user,
        app_id=args.app_id,
    ))

    print("\n" + "=" * 60)
    passed = sum(1 for r in results if r)
    total = len(results)
    print(f"Summary: {passed}/{total} scenarios matched expected behavior")
    print("=" * 60)
    if passed == total:
        print("\n✅ Handler logic is intact. If real Feishu E2E still fails,")
        print("   the issue is likely in one of these (NOT the handler):")
        print("   - Feishu event subscription not enabled in developer console")
        print("   - bot_sent_message_ids empty (bot hadn't sent a message this")
        print("     instance-uptime, so reaction target isn't 'recognized')")
        print("   - User reacted to a message older than 1 hour")
        print("   - Active session for that user expired (Redis TTL)")
        return 0
    print("\n❌ Handler logic has bugs. See failed scenarios above.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
