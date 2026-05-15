from __future__ import annotations

from unittest.mock import AsyncMock

from pyclaw.core.memory_archive import archive_session_background
from pyclaw.models import MessageEntry, now_iso
from pyclaw.storage.session.base import InMemorySessionStore


async def _populate_session(
    store: InMemorySessionStore,
    session_key: str,
    user_msg_count: int,
) -> str:
    tree = await store.create_new_session(session_key, "default", "default")
    session_id = tree.header.id
    prior: str | None = None
    for i in range(user_msg_count):
        u = MessageEntry(
            id=f"u{i}",
            parent_id=prior,
            timestamp=now_iso(),
            role="user",
            content=f"user msg {i}",
        )
        await store.append_entry(session_id, u, leaf_id=u.id)
        prior = u.id
        a = MessageEntry(
            id=f"a{i}",
            parent_id=prior,
            timestamp=now_iso(),
            role="assistant",
            content=f"assistant reply {i}",
        )
        await store.append_entry(session_id, a, leaf_id=a.id)
        prior = a.id
    return session_id


async def test_archive_fires_on_rotate_with_sufficient_messages() -> None:
    store = InMemorySessionStore()
    session_id = await _populate_session(store, "feishu:cli:ou_abc", user_msg_count=5)

    ms = AsyncMock()
    await archive_session_background(ms, store, session_id)

    ms.archive_session.assert_awaited_once()
    call_args = ms.archive_session.await_args
    assert call_args.args[0] == "feishu:cli:ou_abc"
    assert call_args.args[1] == session_id
    summary = call_args.args[2]
    assert isinstance(summary, str) and len(summary) > 0


async def test_archive_skipped_when_session_too_short() -> None:
    store = InMemorySessionStore()
    session_id = await _populate_session(store, "feishu:cli:ou_short", user_msg_count=2)

    ms = AsyncMock()
    await archive_session_background(ms, store, session_id)

    ms.archive_session.assert_not_awaited()


async def test_archive_skipped_when_session_not_found() -> None:
    store = InMemorySessionStore()
    ms = AsyncMock()

    await archive_session_background(ms, store, "nonexistent:session:id")

    ms.archive_session.assert_not_awaited()


async def test_archive_failure_does_not_propagate() -> None:
    store = InMemorySessionStore()
    session_id = await _populate_session(store, "feishu:cli:ou_fail", user_msg_count=5)

    ms = AsyncMock()
    ms.archive_session.side_effect = RuntimeError("storage down")

    await archive_session_background(ms, store, session_id)

    ms.archive_session.assert_awaited_once()


async def test_archive_summary_handles_list_content_with_image() -> None:
    from pyclaw.models import ImageBlock, TextBlock

    store = InMemorySessionStore()
    tree = await store.create_new_session("feishu:cli:ou_img", "default", "default")
    session_id = tree.header.id
    prior: str | None = None
    for i in range(5):
        u = MessageEntry(
            id=f"u{i}",
            parent_id=prior,
            timestamp=now_iso(),
            role="user",
            content=[
                ImageBlock(type="image", data="b64", mime_type="image/png"),
                TextBlock(type="text", text=f"what is image {i}"),
            ],
        )
        await store.append_entry(session_id, u, leaf_id=u.id)
        prior = u.id
        a = MessageEntry(
            id=f"a{i}",
            parent_id=prior,
            timestamp=now_iso(),
            role="assistant",
            content=f"I see image {i}",
        )
        await store.append_entry(session_id, a, leaf_id=a.id)
        prior = a.id

    ms = AsyncMock()
    await archive_session_background(ms, store, session_id)

    ms.archive_session.assert_awaited_once()
    summary = ms.archive_session.await_args.args[2]
    assert "what is image" in summary
    assert "[图片]" in summary
    assert "I see image" in summary


async def test_archive_summary_pure_image_user_turns_not_silently_dropped() -> None:
    from pyclaw.models import ImageBlock

    store = InMemorySessionStore()
    tree = await store.create_new_session("feishu:cli:ou_pure_img", "default", "default")
    session_id = tree.header.id
    prior: str | None = None
    for i in range(5):
        u = MessageEntry(
            id=f"u{i}",
            parent_id=prior,
            timestamp=now_iso(),
            role="user",
            content=[ImageBlock(type="image", data="b64", mime_type="image/png")],
        )
        await store.append_entry(session_id, u, leaf_id=u.id)
        prior = u.id
        a = MessageEntry(
            id=f"a{i}",
            parent_id=prior,
            timestamp=now_iso(),
            role="assistant",
            content=f"assistant {i}",
        )
        await store.append_entry(session_id, a, leaf_id=a.id)
        prior = a.id

    ms = AsyncMock()
    await archive_session_background(ms, store, session_id)

    summary = ms.archive_session.await_args.args[2]
    assert summary.count("user: [图片]") >= 5


async def test_archive_summary_truncates_long_content() -> None:
    store = InMemorySessionStore()
    tree = await store.create_new_session("feishu:cli:ou_long", "default", "default")
    session_id = tree.header.id
    prior: str | None = None
    for i in range(5):
        u = MessageEntry(
            id=f"u{i}",
            parent_id=prior,
            timestamp=now_iso(),
            role="user",
            content="X" * 1000,
        )
        await store.append_entry(session_id, u, leaf_id=u.id)
        prior = u.id
        a = MessageEntry(
            id=f"a{i}",
            parent_id=prior,
            timestamp=now_iso(),
            role="assistant",
            content="Y" * 1000,
        )
        await store.append_entry(session_id, a, leaf_id=a.id)
        prior = a.id

    ms = AsyncMock()
    await archive_session_background(ms, store, session_id)

    ms.archive_session.assert_awaited_once()
    summary = ms.archive_session.await_args.args[2]
    assert len(summary) < 1200
    assert "..." in summary
