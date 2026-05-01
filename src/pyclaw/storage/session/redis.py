from __future__ import annotations

import json
import logging
import time

import redis.asyncio as aioredis

from pyclaw.models import SessionEntry, SessionHeader, SessionTree
from pyclaw.models.session import (
    CompactionEntry,
    CustomEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionHistorySummary,
    now_iso,
)
from pyclaw.storage.lock.redis import LockAcquireError, RedisLockManager
from pyclaw.storage.protocols import LockManager
from pyclaw.storage.session.base import _generate_session_id

logger = logging.getLogger(__name__)


class SessionLockError(Exception):
    def __init__(self, session_id: str) -> None:
        super().__init__(f"could not acquire write lock for session {session_id!r}")
        self.session_id = session_id


class RedisSessionStore:
    def __init__(
        self,
        client: aioredis.Redis,
        lock_manager: LockManager,
        *,
        ttl_seconds: int = 604_800,
        key_prefix: str = "pyclaw:",
    ) -> None:
        self._client = client
        self._lock = lock_manager
        self._ttl = ttl_seconds
        self._prefix = key_prefix

    def _hdr_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:header"

    def _entries_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:entries"

    def _order_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:order"

    def _leaf_key(self, sid: str) -> str:
        return f"{self._prefix}session:{{{sid}}}:leaf"

    def _lock_key(self, sid: str) -> str:
        return f"session-lock:{{{sid}}}"

    def _skey_current_key(self, session_key: str) -> str:
        return f"{self._prefix}skey:{{{session_key}}}:current"

    def _skey_history_key(self, session_key: str) -> str:
        return f"{self._prefix}skey:{{{session_key}}}:history"

    async def save_header(self, tree: SessionTree) -> None:
        sid = tree.header.id
        hdr_json = tree.header.model_dump_json()
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.set(self._hdr_key(sid), hdr_json, ex=self._ttl)
            pipe.expire(self._entries_key(sid), self._ttl)
            pipe.expire(self._order_key(sid), self._ttl)
            pipe.expire(self._leaf_key(sid), self._ttl)
            await pipe.execute()

    async def load(self, session_id: str) -> SessionTree | None:
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.get(self._hdr_key(session_id))
            pipe.hgetall(self._entries_key(session_id))
            pipe.lrange(self._order_key(session_id), 0, -1)
            pipe.get(self._leaf_key(session_id))
            hdr_raw, entries_raw, order_raw, leaf_raw = await pipe.execute()

        if hdr_raw is None:
            return None

        header = SessionHeader.model_validate_json(hdr_raw)
        entries: dict[str, SessionEntry] = {}
        for entry_id, entry_json in (entries_raw or {}).items():
            entry = _parse_entry(entry_json)
            if entry is not None:
                entries[entry_id] = entry

        return SessionTree(
            header=header,
            entries=entries,
            order=list(order_raw or []),
            leaf_id=leaf_raw or None,
        )

    async def append_entry(
        self, session_id: str, entry: SessionEntry, leaf_id: str
    ) -> None:
        lock_key = self._lock_key(session_id)
        try:
            token = await self._lock.acquire(lock_key, ttl_ms=30_000)
        except LockAcquireError as exc:
            raise SessionLockError(session_id) from exc

        try:
            entry_json = _serialize_entry(entry)
            async with self._client.pipeline(transaction=False) as pipe:
                pipe.hset(self._entries_key(session_id), entry.id, entry_json)
                pipe.rpush(self._order_key(session_id), entry.id)
                pipe.set(self._leaf_key(session_id), leaf_id)
                pipe.expire(self._hdr_key(session_id), self._ttl)
                pipe.expire(self._entries_key(session_id), self._ttl)
                pipe.expire(self._order_key(session_id), self._ttl)
                pipe.expire(self._leaf_key(session_id), self._ttl)
                await pipe.execute()
        finally:
            await self._lock.release(lock_key, token)


    async def get_current_session_id(self, session_key: str) -> str | None:
        val = await self._client.get(self._skey_current_key(session_key))
        if val is None:
            return None
        return val.decode() if isinstance(val, bytes) else val

    async def set_current_session_id(self, session_key: str, session_id: str) -> None:
        score = time.time() * 1000
        async with self._client.pipeline(transaction=False) as pipe:
            pipe.set(self._skey_current_key(session_key), session_id)
            pipe.zadd(self._skey_history_key(session_key), {session_id: score})
            await pipe.execute()

    async def create_new_session(
        self,
        session_key: str,
        workspace_id: str,
        agent_id: str,
        parent_session_id: str | None = None,
    ) -> SessionTree:
        session_id = _generate_session_id(session_key)
        header = SessionHeader(
            id=session_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            session_key=session_key,
            parent_session=parent_session_id,
        )
        tree = SessionTree(header=header)
        await self.save_header(tree)
        await self.set_current_session_id(session_key, session_id)
        return tree

    async def list_session_history(
        self, session_key: str, limit: int = 20
    ) -> list[SessionHistorySummary]:
        raw = await self._client.zrevrangebyscore(
            self._skey_history_key(session_key), "+inf", "-inf",
            start=0, num=limit,
        )
        result: list[SessionHistorySummary] = []
        for sid_bytes in raw:
            sid = sid_bytes.decode() if isinstance(sid_bytes, bytes) else sid_bytes
            hdr_raw = await self._client.get(self._hdr_key(sid))
            if hdr_raw is None:
                result.append(SessionHistorySummary(
                    session_id=sid, created_at="",
                    message_count=0, last_message_at=None, parent_session_id=None,
                ))
                continue
            header = SessionHeader.model_validate_json(hdr_raw)
            msg_count = await self._client.hlen(self._entries_key(sid))
            result.append(SessionHistorySummary(
                session_id=sid,
                created_at=header.created_at,
                message_count=int(msg_count),
                last_message_at=header.last_interaction_at,
                parent_session_id=header.parent_session,
            ))
        return result


def _serialize_entry(entry: SessionEntry) -> str:
    if hasattr(entry, "model_dump_json"):
        return entry.model_dump_json()
    return json.dumps(entry)


def _parse_entry(raw: str) -> SessionEntry | None:
    try:
        data = json.loads(raw)
        entry_type = data.get("type")
        if entry_type == "message":
            return MessageEntry.model_validate(data)
        if entry_type == "compaction":
            return CompactionEntry.model_validate(data)
        if entry_type == "model_change":
            return ModelChangeEntry.model_validate(data)
        if entry_type == "custom":
            return CustomEntry.model_validate(data)
        logger.warning("unknown entry type %r — skipping", entry_type)
        return None
    except Exception:
        logger.exception("failed to parse session entry")
        return None
