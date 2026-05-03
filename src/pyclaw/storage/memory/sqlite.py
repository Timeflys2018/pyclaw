from __future__ import annotations

import logging
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from pyclaw.storage.memory.base import ArchiveEntry, MemoryEntry

if TYPE_CHECKING:
    from pyclaw.storage.memory.embedding import EmbeddingClient

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS facts (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    source_session_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(type);

CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
    content,
    content=facts,
    content_rowid=rowid,
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    INSERT INTO facts_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TABLE IF NOT EXISTS procedures (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    source_session_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_used_at REAL,
    use_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active'
);
CREATE INDEX IF NOT EXISTS idx_procedures_status ON procedures(status);

CREATE VIRTUAL TABLE IF NOT EXISTS procedures_fts USING fts5(
    content,
    content=procedures,
    content_rowid=rowid,
    tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS procedures_ai AFTER INSERT ON procedures BEGIN
    INSERT INTO procedures_fts(rowid, content) VALUES (new.rowid, new.content);
END;
CREATE TRIGGER IF NOT EXISTS procedures_ad AFTER DELETE ON procedures BEGIN
    INSERT INTO procedures_fts(procedures_fts, rowid, content)
        VALUES('delete', old.rowid, old.content);
END;
CREATE TRIGGER IF NOT EXISTS procedures_au AFTER UPDATE ON procedures BEGIN
    INSERT INTO procedures_fts(procedures_fts, rowid, content)
        VALUES('delete', old.rowid, old.content);
    INSERT INTO procedures_fts(rowid, content) VALUES (new.rowid, new.content);
END;

CREATE TABLE IF NOT EXISTS archives (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at REAL NOT NULL,
    token_count INTEGER
);
"""


def _escape_fts5_query(query: str) -> str:
    escaped = query.replace('"', '""')
    return f'"{escaped}"'


class SqliteMemoryBackend:
    def __init__(self, base_dir: Path, embedding: EmbeddingClient | None = None) -> None:
        self._base_dir = base_dir
        self._embedding = embedding
        self._connections: dict[str, aiosqlite.Connection] = {}
        self._vec_loaded: set[str] = set()

    async def _get_conn(self, session_key: str) -> aiosqlite.Connection:
        if session_key in self._connections:
            return self._connections[session_key]
        db_name = session_key.replace(":", "_") + ".db"
        db_path = self._base_dir / db_name
        conn = await aiosqlite.connect(db_path)
        conn.row_factory = aiosqlite.Row
        await self._ensure_schema(conn)
        self._connections[session_key] = conn
        return conn

    async def _ensure_schema(self, conn: aiosqlite.Connection) -> None:
        await conn.executescript(_SCHEMA_SQL)

    async def _ensure_vec(self, session_key: str, conn: aiosqlite.Connection) -> bool:
        if session_key in self._vec_loaded:
            return True
        try:
            import sqlite_vec

            await conn.enable_load_extension(True)
            await conn.load_extension(sqlite_vec.loadable_path())
            await conn.enable_load_extension(False)
            if self._embedding:
                await conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS archives_vec USING vec0("
                    "    id TEXT PRIMARY KEY,"
                    f"    embedding float[{self._embedding.dimensions}] distance_metric=cosine"
                    ")"
                )
                await conn.commit()
            self._vec_loaded.add(session_key)
            return True
        except Exception:
            logger.warning("sqlite-vec not available, vector search disabled")
            return False

    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        conn = await self._get_conn(session_key)
        if entry.layer == "L2":
            await conn.execute(
                "INSERT INTO facts "
                "(id, session_key, type, content, source_session_id, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "type=excluded.type, content=excluded.content, "
                "source_session_id=excluded.source_session_id, updated_at=excluded.updated_at",
                (
                    entry.id,
                    session_key,
                    entry.type,
                    entry.content,
                    entry.source_session_id,
                    entry.created_at,
                    entry.updated_at,
                ),
            )
        elif entry.layer == "L3":
            await conn.execute(
                "INSERT INTO procedures "
                "(id, session_key, type, content, source_session_id, "
                "created_at, updated_at, last_used_at, use_count, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "type=excluded.type, content=excluded.content, "
                "source_session_id=excluded.source_session_id, "
                "updated_at=excluded.updated_at, last_used_at=excluded.last_used_at, "
                "use_count=excluded.use_count, status=excluded.status",
                (
                    entry.id,
                    session_key,
                    entry.type,
                    entry.content,
                    entry.source_session_id,
                    entry.created_at,
                    entry.updated_at,
                    entry.last_used_at,
                    entry.use_count,
                    entry.status,
                ),
            )
        else:
            msg = f"SqliteMemoryBackend only handles L2/L3, got {entry.layer!r}"
            raise ValueError(msg)
        await conn.commit()

    async def search(
        self,
        session_key: str,
        query: str,
        *,
        layers: list[str] | None = None,
        limit: int = 10,
    ) -> list[MemoryEntry]:
        if layers is None:
            layers = ["L2", "L3"]
        conn = await self._get_conn(session_key)
        use_like = len(query) < 3
        results: list[MemoryEntry] = []

        if "L2" in layers:
            rows = await self._search_table(conn, "facts", "facts_fts", query, use_like, limit)
            for row in rows:
                results.append(
                    MemoryEntry(
                        id=row["id"],
                        layer="L2",
                        type=row["type"],
                        content=row["content"],
                        source_session_id=row["source_session_id"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                    )
                )

        if "L3" in layers:
            rows = await self._search_procedures(conn, query, use_like, limit)
            for row in rows:
                results.append(
                    MemoryEntry(
                        id=row["id"],
                        layer="L3",
                        type=row["type"],
                        content=row["content"],
                        source_session_id=row["source_session_id"],
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        last_used_at=row["last_used_at"],
                        use_count=row["use_count"],
                        status=row["status"],
                    )
                )

        return results[:limit]

    async def _search_table(
        self,
        conn: aiosqlite.Connection,
        table: str,
        fts_table: str,
        query: str,
        use_like: bool,
        limit: int,
    ) -> list[aiosqlite.Row]:
        if use_like:
            cursor = await conn.execute(
                f"SELECT * FROM {table} WHERE content LIKE ? LIMIT ?",  # noqa: S608
                (f"%{query}%", limit),
            )
        else:
            cursor = await conn.execute(
                f"SELECT t.* FROM {table} t "  # noqa: S608
                f"JOIN {fts_table} f ON t.rowid = f.rowid "
                f"WHERE {fts_table} MATCH ? LIMIT ?",
                (_escape_fts5_query(query), limit),
            )
        return await cursor.fetchall()  # type: ignore[return-value]

    async def _search_procedures(
        self,
        conn: aiosqlite.Connection,
        query: str,
        use_like: bool,
        limit: int,
    ) -> list[aiosqlite.Row]:
        if use_like:
            cursor = await conn.execute(
                "SELECT * FROM procedures WHERE content LIKE ? AND status = 'active' LIMIT ?",
                (f"%{query}%", limit),
            )
        else:
            cursor = await conn.execute(
                "SELECT t.* FROM procedures t "
                "JOIN procedures_fts f ON t.rowid = f.rowid "
                "WHERE procedures_fts MATCH ? AND t.status = 'active' LIMIT ?",
                (_escape_fts5_query(query), limit),
            )
        return await cursor.fetchall()  # type: ignore[return-value]

    async def delete(self, session_key: str, entry_id: str) -> None:
        conn = await self._get_conn(session_key)
        await conn.execute("DELETE FROM facts WHERE id = ?", (entry_id,))
        await conn.execute("DELETE FROM procedures WHERE id = ?", (entry_id,))
        await conn.commit()

    async def archive_session(
        self, session_key: str, session_id: str, summary: str
    ) -> None:
        conn = await self._get_conn(session_key)
        archive_id = str(uuid.uuid4())
        await conn.execute(
            "INSERT INTO archives (id, session_key, session_id, summary, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (archive_id, session_key, session_id, summary, time.time()),
        )
        if self._embedding and await self._ensure_vec(session_key, conn):
            try:
                import sqlite_vec

                embedding = await self._embedding.embed(summary)
                await conn.execute(
                    "INSERT INTO archives_vec (id, embedding) VALUES (?, ?)",
                    (archive_id, sqlite_vec.serialize_float32(embedding)),
                )
            except Exception:
                logger.warning("Failed to embed archive summary, skipping vector insert")
        await conn.commit()

    async def search_archives(
        self, session_key: str, query: str, *, limit: int = 5
    ) -> list[ArchiveEntry]:
        if not self._embedding:
            return []
        conn = await self._get_conn(session_key)
        if not await self._ensure_vec(session_key, conn):
            return []
        try:
            import sqlite_vec

            embedding = await self._embedding.embed(query)
        except Exception:
            logger.warning("Failed to embed search query, returning empty results")
            return []
        cursor = await conn.execute(
            "SELECT a.id, a.session_id, a.summary, a.created_at, v.distance "
            "FROM archives_vec v "
            "JOIN archives a ON a.id = v.id "
            "WHERE v.embedding MATCH ? AND k = ? "
            "ORDER BY v.distance",
            (sqlite_vec.serialize_float32(embedding), limit),
        )
        rows = await cursor.fetchall()
        return [
            ArchiveEntry(
                id=row["id"],
                session_id=row["session_id"],
                summary=row["summary"],
                created_at=row["created_at"],
                distance=row["distance"],
            )
            for row in rows
        ]

    async def close(self) -> None:
        for conn in self._connections.values():
            await conn.close()
        self._connections.clear()
        self._vec_loaded.clear()
