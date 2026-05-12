from __future__ import annotations

import asyncio
import logging
import time
import uuid
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import apsw

from pyclaw.storage.memory.base import ArchiveEntry, MemoryEntry
from pyclaw.storage.memory.jieba_tokenizer import (
    build_safe_match_query,
    register_jieba_tokenizer,
)
from pyclaw.storage.memory.naming import DbFileNamingPolicy, HumanReadableNaming

if TYPE_CHECKING:
    from pyclaw.storage.memory.embedding import EmbeddingClient

logger = logging.getLogger(__name__)

_SCHEMA_SQL_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS facts (
        id TEXT PRIMARY KEY,
        session_key TEXT NOT NULL,
        type TEXT NOT NULL,
        content TEXT NOT NULL,
        source_session_id TEXT,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS idx_facts_type ON facts(type)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
        content,
        content=facts,
        content_rowid=rowid,
        tokenize='jieba'
    )""",
    """CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
        INSERT INTO facts_fts(rowid, content) VALUES (new.rowid, new.content);
    END""",
    """CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.rowid, old.content);
    END""",
    """CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
        INSERT INTO facts_fts(facts_fts, rowid, content) VALUES('delete', old.rowid, old.content);
        INSERT INTO facts_fts(rowid, content) VALUES (new.rowid, new.content);
    END""",
    """CREATE TABLE IF NOT EXISTS procedures (
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
    )""",
    "CREATE INDEX IF NOT EXISTS idx_procedures_status ON procedures(status)",
    """CREATE VIRTUAL TABLE IF NOT EXISTS procedures_fts USING fts5(
        content,
        content=procedures,
        content_rowid=rowid,
        tokenize='jieba'
    )""",
    """CREATE TRIGGER IF NOT EXISTS procedures_ai AFTER INSERT ON procedures BEGIN
        INSERT INTO procedures_fts(rowid, content) VALUES (new.rowid, new.content);
    END""",
    """CREATE TRIGGER IF NOT EXISTS procedures_ad AFTER DELETE ON procedures BEGIN
        INSERT INTO procedures_fts(procedures_fts, rowid, content)
            VALUES('delete', old.rowid, old.content);
    END""",
    """CREATE TRIGGER IF NOT EXISTS procedures_au AFTER UPDATE OF content ON procedures BEGIN
        INSERT INTO procedures_fts(procedures_fts, rowid, content)
            VALUES('delete', old.rowid, old.content);
        INSERT INTO procedures_fts(rowid, content) VALUES (new.rowid, new.content);
    END""",
    """CREATE TABLE IF NOT EXISTS archives (
        id TEXT PRIMARY KEY,
        session_key TEXT NOT NULL,
        session_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        created_at REAL NOT NULL,
        token_count INTEGER
    )""",
]


def _dict_row(cursor: apsw.Cursor, row: tuple) -> dict[str, Any]:
    description = cursor.getdescription()
    return {desc[0]: val for desc, val in zip(description, row)}


class SqliteMemoryBackend:
    def __init__(
        self,
        base_dir: Path,
        embedding: EmbeddingClient | None = None,
        *,
        fts_min_query_chars: int = 3,
        naming: DbFileNamingPolicy | None = None,
    ) -> None:
        self._base_dir = base_dir
        self._embedding = embedding
        self._fts_min_query_chars = fts_min_query_chars
        self._naming: DbFileNamingPolicy = naming or HumanReadableNaming()
        self._connections: dict[str, apsw.Connection] = {}
        self._vec_loaded: set[str] = set()
        self._migrated: set[str] = set()

    def _get_conn_sync(self, session_key: str) -> apsw.Connection:
        if session_key in self._connections:
            return self._connections[session_key]
        db_name = self._naming.filename_for(session_key)
        db_path = self._base_dir / db_name
        resolved = db_path.resolve()
        if not resolved.is_relative_to(self._base_dir.resolve()):
            raise ValueError(
                f"session_key {session_key!r} produced out-of-base db path: {resolved}",
            )
        conn = apsw.Connection(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        register_jieba_tokenizer(conn)
        self._ensure_schema_sync(conn, session_key)
        self._connections[session_key] = conn
        return conn

    async def _get_conn(self, session_key: str) -> apsw.Connection:
        if session_key in self._connections:
            return self._connections[session_key]
        return await asyncio.to_thread(self._get_conn_sync, session_key)

    def _ensure_schema_sync(self, conn: apsw.Connection, session_key: str) -> None:
        if session_key in self._migrated:
            return
        self._needs_rebuild = False
        self._maybe_migrate_fts(conn)
        for stmt in _SCHEMA_SQL_STATEMENTS:
            try:
                conn.execute(stmt)
            except apsw.SQLError:
                pass
        if self._needs_rebuild:
            try:
                conn.execute("INSERT INTO facts_fts(facts_fts) VALUES('rebuild')")
            except apsw.SQLError:
                pass
            try:
                conn.execute("INSERT INTO procedures_fts(procedures_fts) VALUES('rebuild')")
            except apsw.SQLError:
                pass
        self._migrate_procedures_trigger(conn)
        self._migrate_add_archived_at(conn)
        self._migrated.add(session_key)

    def _migrate_add_archived_at(self, conn: apsw.Connection) -> None:
        """Add archived_at and archive_reason columns for curator lifecycle."""
        try:
            conn.execute("ALTER TABLE procedures ADD COLUMN archived_at REAL")
        except apsw.SQLError:
            pass  # Column already exists
        try:
            conn.execute("ALTER TABLE procedures ADD COLUMN archive_reason TEXT")
        except apsw.SQLError:
            pass  # Column already exists

    def _migrate_procedures_trigger(self, conn: apsw.Connection) -> None:
        """Migrate procedures_au trigger to only fire on content changes.

        This prevents use_count/last_used_at UPDATEs from triggering
        wasteful FTS5 re-indexing of unchanged content.
        """
        conn.execute("DROP TRIGGER IF EXISTS procedures_au")
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS procedures_au "
            "AFTER UPDATE OF content ON procedures BEGIN "
            "INSERT INTO procedures_fts(procedures_fts, rowid, content) "
            "VALUES('delete', old.rowid, old.content); "
            "INSERT INTO procedures_fts(rowid, content) "
            "VALUES (new.rowid, new.content); END"
        )

    def _maybe_migrate_fts(self, conn: apsw.Connection) -> None:
        try:
            row = list(conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='facts_fts'"
            ))
            if row and "trigram" in (row[0][0] or ""):
                logger.info("migrating FTS5 from trigram to jieba tokenizer")
                conn.execute("DROP TABLE IF EXISTS facts_fts")
                conn.execute("DROP TABLE IF EXISTS procedures_fts")
                conn.execute("DROP TRIGGER IF EXISTS facts_ai")
                conn.execute("DROP TRIGGER IF EXISTS facts_ad")
                conn.execute("DROP TRIGGER IF EXISTS facts_au")
                conn.execute("DROP TRIGGER IF EXISTS procedures_ai")
                conn.execute("DROP TRIGGER IF EXISTS procedures_ad")
                conn.execute("DROP TRIGGER IF EXISTS procedures_au")
                self._needs_rebuild = True
            else:
                self._needs_rebuild = False
        except apsw.SQLError:
            self._needs_rebuild = False

    async def _ensure_vec(self, session_key: str, conn: apsw.Connection) -> bool:
        if session_key in self._vec_loaded:
            return True

        def _load_vec() -> bool:
            try:
                import sqlite_vec

                conn.enable_load_extension(True)
                conn.load_extension(sqlite_vec.loadable_path())
                conn.enable_load_extension(False)
                if self._embedding:
                    conn.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS archives_vec USING vec0("
                        "    id TEXT PRIMARY KEY,"
                        f"    embedding float[{self._embedding.dimensions}] distance_metric=cosine"
                        ")"
                    )
                return True
            except Exception:
                logger.warning("sqlite-vec not available, vector search disabled")
                return False

        result = await asyncio.to_thread(_load_vec)
        if result:
            self._vec_loaded.add(session_key)
        return result

    async def store(self, session_key: str, entry: MemoryEntry) -> None:
        conn = await self._get_conn(session_key)

        def _store_sync() -> None:
            if entry.layer == "L2":
                conn.execute(
                    "INSERT INTO facts "
                    "(id, session_key, type, content, source_session_id, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "type=excluded.type, content=excluded.content, "
                    "source_session_id=excluded.source_session_id, updated_at=excluded.updated_at",
                    (
                        entry.id, session_key, entry.type, entry.content,
                        entry.source_session_id, entry.created_at, entry.updated_at,
                    ),
                )
            elif entry.layer == "L3":
                conn.execute(
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
                        entry.id, session_key, entry.type, entry.content,
                        entry.source_session_id, entry.created_at, entry.updated_at,
                        entry.last_used_at, entry.use_count, entry.status,
                    ),
                )
            else:
                msg = f"SqliteMemoryBackend only handles L2/L3, got {entry.layer!r}"
                raise ValueError(msg)

        await asyncio.to_thread(_store_sync)

    async def search(
        self,
        session_key: str,
        query: str,
        *,
        layers: list[str] | None = None,
        limit: int = 10,
        per_layer_limits: dict[str, int] | None = None,
    ) -> list[MemoryEntry]:
        if layers is None:
            layers = ["L2", "L3"]
        conn = await self._get_conn(session_key)
        use_like = len(query) < self._fts_min_query_chars

        def _search_sync() -> list[MemoryEntry]:
            results: list[MemoryEntry] = []

            if "L2" in layers:
                layer_limit = (per_layer_limits or {}).get("L2", limit)
                rows = self._search_table_sync(conn, "facts", "facts_fts", query, use_like, layer_limit)
                for row in rows:
                    results.append(
                        MemoryEntry(
                            id=row["id"], layer="L2", type=row["type"],
                            content=row["content"],
                            source_session_id=row["source_session_id"],
                            created_at=row["created_at"], updated_at=row["updated_at"],
                            score=row.get("_score"),
                        )
                    )

            if "L3" in layers:
                layer_limit = (per_layer_limits or {}).get("L3", limit)
                rows = self._search_procedures_sync(conn, query, use_like, layer_limit)
                for row in rows:
                    results.append(
                        MemoryEntry(
                            id=row["id"], layer="L3", type=row["type"],
                            content=row["content"],
                            source_session_id=row["source_session_id"],
                            created_at=row["created_at"], updated_at=row["updated_at"],
                            last_used_at=row["last_used_at"],
                            use_count=row["use_count"], status=row["status"],
                            score=row.get("_score"),
                        )
                    )

            if per_layer_limits:
                return results
            return results[:limit]

        return await asyncio.to_thread(_search_sync)

    def _search_table_sync(
        self, conn: apsw.Connection, table: str, fts_table: str,
        query: str, use_like: bool, limit: int,
    ) -> list[dict[str, Any]]:
        if use_like:
            cursor = conn.execute(
                f"SELECT * FROM {table} WHERE content LIKE ? ORDER BY updated_at DESC LIMIT ?",
                (f"%{query}%", limit),
            )
        else:
            match_query = build_safe_match_query(query)
            if match_query is None:
                return []
            cursor = conn.execute(
                f"SELECT t.*, f.rank AS _score FROM {table} t "
                f"JOIN {fts_table} f ON t.rowid = f.rowid "
                f"WHERE {fts_table} MATCH ? ORDER BY f.rank LIMIT ?",
                (match_query, limit),
            )
        return [_dict_row(cursor, row) for row in cursor]

    def _search_procedures_sync(
        self, conn: apsw.Connection, query: str, use_like: bool, limit: int,
    ) -> list[dict[str, Any]]:
        if use_like:
            cursor = conn.execute(
                "SELECT * FROM procedures WHERE content LIKE ? AND status = 'active' "
                "ORDER BY updated_at DESC LIMIT ?",
                (f"%{query}%", limit),
            )
        else:
            match_query = build_safe_match_query(query)
            if match_query is None:
                return []
            cursor = conn.execute(
                "SELECT t.*, f.rank AS _score FROM procedures t "
                "JOIN procedures_fts f ON t.rowid = f.rowid "
                "WHERE procedures_fts MATCH ? AND t.status = 'active' ORDER BY f.rank LIMIT ?",
                (match_query, limit),
            )
        rows = [_dict_row(cursor, row) for row in cursor]

        # Activate lifecycle fields: bump use_count and update last_used_at
        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join(["?"] * len(ids))
            now = time.time()
            conn.execute(
                f"UPDATE procedures SET use_count = use_count + 1, "
                f"last_used_at = ? WHERE id IN ({placeholders})",
                (now, *ids),
            )
            for r in rows:
                r["use_count"] = (r.get("use_count") or 0) + 1
                r["last_used_at"] = now

        return rows

    async def delete(self, session_key: str, entry_id: str) -> None:
        conn = await self._get_conn(session_key)

        def _delete_sync() -> None:
            conn.execute("DELETE FROM facts WHERE id = ?", (entry_id,))
            conn.execute("DELETE FROM procedures WHERE id = ?", (entry_id,))

        await asyncio.to_thread(_delete_sync)

    async def archive_session(
        self, session_key: str, session_id: str, summary: str
    ) -> None:
        conn = await self._get_conn(session_key)

        def _archive_sync() -> None:
            archive_id = str(uuid.uuid4())
            conn.execute(
                "INSERT INTO archives (id, session_key, session_id, summary, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (archive_id, session_key, session_id, summary, time.time()),
            )

        await asyncio.to_thread(_archive_sync)

        if self._embedding and await self._ensure_vec(session_key, conn):
            try:
                import sqlite_vec

                embedding = await self._embedding.embed(summary)

                def _vec_insert() -> None:
                    archive_id = list(conn.execute(
                        "SELECT id FROM archives WHERE session_id = ? ORDER BY created_at DESC LIMIT 1",
                        (session_id,),
                    ))[0][0]
                    conn.execute(
                        "INSERT INTO archives_vec (id, embedding) VALUES (?, ?)",
                        (archive_id, sqlite_vec.serialize_float32(embedding)),
                    )

                await asyncio.to_thread(_vec_insert)
            except Exception:
                logger.warning("Failed to embed archive summary, skipping vector insert")

    async def search_archives(
        self, session_key: str, query: str, *, limit: int = 5, min_similarity: float = 0.0
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

        overfetch = max(limit, limit * 2) if min_similarity > 0 else limit

        def _search_vec() -> list[ArchiveEntry]:
            cursor = conn.execute(
                "SELECT a.id, a.session_id, a.summary, a.created_at, v.distance "
                "FROM archives_vec v "
                "JOIN archives a ON a.id = v.id "
                "WHERE v.embedding MATCH ? AND k = ? "
                "ORDER BY v.distance",
                (sqlite_vec.serialize_float32(embedding), overfetch),
            )
            max_distance = 1.0 - min_similarity
            results = []
            for row in cursor:
                row_dict = _dict_row(cursor, row)
                distance = row_dict["distance"]
                if min_similarity > 0 and distance > max_distance:
                    continue
                results.append(
                    ArchiveEntry(
                        id=row_dict["id"],
                        session_id=row_dict["session_id"],
                        summary=row_dict["summary"],
                        created_at=row_dict["created_at"],
                        distance=distance,
                        similarity=1.0 - distance,
                    )
                )
            return results[:limit]

        return await asyncio.to_thread(_search_vec)

    async def count_by_layer(self, session_key: str) -> dict[str, int]:
        conn = await self._get_conn(session_key)

        def _count_sync() -> dict[str, int]:
            facts_row = list(conn.execute(
                "SELECT COUNT(*) FROM facts WHERE session_key = ?", (session_key,)
            ))
            procs_row = list(conn.execute(
                "SELECT COUNT(*) FROM procedures WHERE session_key = ? AND status = 'active'",
                (session_key,),
            ))
            archives_row = list(conn.execute(
                "SELECT COUNT(*) FROM archives WHERE session_key = ?", (session_key,)
            ))
            return {
                "l2": int(facts_row[0][0]) if facts_row else 0,
                "l3": int(procs_row[0][0]) if procs_row else 0,
                "l4": int(archives_row[0][0]) if archives_row else 0,
            }

        return await asyncio.to_thread(_count_sync)

    async def close(self) -> None:
        for conn in self._connections.values():
            try:
                conn.close()
            except Exception:
                pass
        self._connections.clear()
