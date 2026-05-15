from __future__ import annotations

import logging
from typing import Any

from pyclaw.models import MessageEntry
from pyclaw.storage.memory.base import MemoryStore
from pyclaw.storage.protocols import SessionStore

logger = logging.getLogger(__name__)

MIN_USER_MESSAGES_FOR_ARCHIVE = 3
SIMPLE_SUMMARY_HEAD = 500
SIMPLE_SUMMARY_TAIL = 500


def _derive_session_key(session_id: str) -> str:
    idx = session_id.find(":s:")
    return session_id[:idx] if idx != -1 else session_id


def _count_user_messages(tree: Any) -> int:
    count = 0
    for entry in tree.entries.values():
        if isinstance(entry, MessageEntry) and entry.role == "user":
            count += 1
    return count


def _simple_summary(tree: Any) -> str:
    from pyclaw.models.utils import extract_text_from_content

    texts: list[str] = []
    for entry in tree.entries.values():
        if not isinstance(entry, MessageEntry):
            continue
        if entry.role not in ("user", "assistant"):
            continue
        text = extract_text_from_content(entry.content)
        if text.strip():
            texts.append(f"{entry.role}: {text}")
    joined = "\n".join(texts)
    if len(joined) <= SIMPLE_SUMMARY_HEAD + SIMPLE_SUMMARY_TAIL + 10:
        return joined
    return joined[:SIMPLE_SUMMARY_HEAD] + "\n...\n" + joined[-SIMPLE_SUMMARY_TAIL:]


async def archive_session_background(
    memory_store: MemoryStore,
    session_store: SessionStore,
    old_session_id: str,
) -> None:
    try:
        tree = await session_store.load(old_session_id)
        if tree is None:
            logger.info("archive skipped: session %s not found", old_session_id)
            return

        user_msgs = _count_user_messages(tree)
        if user_msgs < MIN_USER_MESSAGES_FOR_ARCHIVE:
            logger.info(
                "archive skipped: session %s has only %d user messages (< %d)",
                old_session_id,
                user_msgs,
                MIN_USER_MESSAGES_FOR_ARCHIVE,
            )
            return

        summary = _simple_summary(tree)
        if not summary:
            logger.info("archive skipped: session %s has no summarizable content", old_session_id)
            return

        session_key = _derive_session_key(old_session_id)
        await memory_store.archive_session(session_key, old_session_id, summary)
        logger.info("archived session %s to L4", old_session_id)
    except Exception:
        logger.warning("archive_session_background failed for %s", old_session_id, exc_info=True)
