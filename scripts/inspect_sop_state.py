"""Usage: .venv/bin/python scripts/inspect_sop_state.py"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

from pyclaw.infra.redis import close_client, get_client
from pyclaw.infra.settings import load_settings


def _format_ttl(ttl: int) -> str:
    if ttl < 0:
        return "no-ttl"
    return f"{ttl}s"


async def _inspect_redis(settings) -> None:  # type: ignore[no-untyped-def]
    client = await get_client(settings.redis)
    try:
        prefix = settings.redis.key_prefix or "pyclaw:"
        for label in ("sop_candidates", "sop_extracting", "sop_ratelimit"):
            pattern = f"{prefix}{label}:*"
            print(f"\n=== {pattern} ===")
            keys: list[str] = []
            async for k in client.scan_iter(match=pattern, count=100):
                keys.append(k)
            if not keys:
                print("  (none)")
                continue
            for key in sorted(keys):
                ttl = await client.ttl(key)
                kind = await client.type(key)
                print(f"\n  KEY: {key}")
                print(f"  TTL: {_format_ttl(ttl)}   TYPE: {kind}")
                if kind == "hash":
                    entries = await client.hgetall(key)
                    print(f"  HLEN: {len(entries)}")
                    for field, raw in entries.items():
                        try:
                            parsed = json.loads(raw)
                            user_msg = parsed.get("user_msg", "")[:60]
                            tools = parsed.get("tool_names", [])
                            ts = parsed.get("timestamp", 0)
                            print(
                                f"    [{field}] tools={tools} ts={ts:.0f} "
                                f"user_msg={user_msg!r}"
                            )
                        except (json.JSONDecodeError, AttributeError):
                            print(f"    [{field}] raw={raw[:100]!r}")
                elif kind == "string":
                    val = await client.get(key)
                    print(f"  VAL: {val!r}")
    finally:
        await close_client(client)


def _inspect_sqlite() -> None:
    db_path = Path("~/.pyclaw/memory/memory.db").expanduser()
    print(f"\n=== SQLite procedures (auto_sop) at {db_path} ===")
    if not db_path.exists():
        print("  (db file not found)")
        return
    try:
        conn = sqlite3.connect(str(db_path))
        rows = list(
            conn.execute(
                "SELECT id, session_key, type, "
                "substr(content, 1, 120), created_at "
                "FROM procedures WHERE type='auto_sop' "
                "ORDER BY created_at DESC LIMIT 10"
            )
        )
        if not rows:
            print("  (no auto_sop rows)")
        for r in rows:
            print(f"  id={r[0]} session_key={r[1]} created={r[4]}")
            print(f"     content[:120]={r[3]!r}")
    except sqlite3.OperationalError as e:
        print(f"  sqlite error: {e}")


async def main() -> None:
    settings = load_settings()
    print(f"Redis: {settings.redis.host}:{settings.redis.port} prefix={settings.redis.key_prefix!r}")
    await _inspect_redis(settings)
    _inspect_sqlite()


if __name__ == "__main__":
    asyncio.run(main())
