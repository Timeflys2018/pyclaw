"""Multi-DB SOP state inspector for real-world testing.

Usage:
    .venv/bin/python scripts/sop_test_inspect.py              # full snapshot
    .venv/bin/python scripts/sop_test_inspect.py --baseline   # save baseline
    .venv/bin/python scripts/sop_test_inspect.py --diff       # diff vs baseline
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

from pyclaw.infra.redis import close_client, get_client
from pyclaw.infra.settings import load_settings


BASELINE_FILE = Path("/tmp/pyclaw_sop_baseline.json")


def _format_ttl(ttl: int) -> str:
    if ttl < 0:
        return "no-ttl"
    return f"{ttl}s"


async def _snapshot_redis(settings: Any) -> dict[str, Any]:
    client = await get_client(settings.redis)
    try:
        prefix = settings.redis.key_prefix or "pyclaw:"
        snapshot: dict[str, Any] = {
            "candidates": {},
            "extracting": {},
            "ratelimit": {},
        }
        for label in ("sop_candidates", "sop_extracting", "sop_ratelimit"):
            pattern = f"{prefix}{label}:*"
            keys: list[str] = []
            async for k in client.scan_iter(match=pattern, count=100):
                keys.append(k)
            for key in sorted(keys):
                ttl = await client.ttl(key)
                kind = await client.type(key)
                short_key = key.removeprefix(prefix)
                if kind == "hash":
                    entries = await client.hgetall(key)
                    parsed = {}
                    for field, raw in entries.items():
                        try:
                            parsed[field] = json.loads(raw)
                        except (json.JSONDecodeError, AttributeError):
                            parsed[field] = {"_raw": str(raw)[:80]}
                    snapshot["candidates"][short_key] = {
                        "ttl": ttl,
                        "count": len(entries),
                        "entries": parsed,
                    }
                elif kind == "string":
                    val = await client.get(key)
                    bucket = "extracting" if "extracting" in label else "ratelimit"
                    snapshot[bucket][short_key] = {"ttl": ttl, "val": val}
        return snapshot
    finally:
        await close_client(client)


def _snapshot_sqlite(memory_base_dir: Path) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "dbs_scanned": [],
        "auto_sop_total": 0,
        "auto_sop_by_db": {},
        "recent_auto_sop": [],
    }
    if not memory_base_dir.exists():
        snapshot["error"] = f"base_dir does not exist: {memory_base_dir}"
        return snapshot

    db_files = sorted(memory_base_dir.glob("*.db"))
    for db in db_files:
        snapshot["dbs_scanned"].append(db.name)
        try:
            conn = sqlite3.connect(str(db))
            try:
                tables = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )]
                if "procedures" not in tables:
                    snapshot["auto_sop_by_db"][db.name] = {
                        "auto_sop_count": 0, "skipped": "no procedures table"
                    }
                    continue

                count_row = conn.execute(
                    "SELECT COUNT(*) FROM procedures WHERE type='auto_sop'"
                ).fetchone()
                count = count_row[0] if count_row else 0
                snapshot["auto_sop_by_db"][db.name] = {"auto_sop_count": count}
                snapshot["auto_sop_total"] += count

                rows = list(conn.execute(
                    "SELECT id, session_key, type, content, "
                    "source_session_id, created_at "
                    "FROM procedures "
                    "WHERE type='auto_sop' "
                    "ORDER BY created_at DESC LIMIT 10"
                ))
                for r in rows:
                    snapshot["recent_auto_sop"].append({
                        "db": db.name,
                        "id": r[0],
                        "session_key": r[1],
                        "source_session_id": r[4],
                        "created_at": r[5],
                        "content_preview": (r[3] or "")[:200],
                    })
            finally:
                conn.close()
        except sqlite3.OperationalError as e:
            snapshot["auto_sop_by_db"][db.name] = {"error": str(e)}

    snapshot["recent_auto_sop"].sort(
        key=lambda x: x.get("created_at", 0), reverse=True
    )
    snapshot["recent_auto_sop"] = snapshot["recent_auto_sop"][:10]
    return snapshot


def _print_snapshot(redis_snap: dict, sqlite_snap: dict) -> None:
    print("\n" + "=" * 70)
    print(f"SNAPSHOT @ {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    print("\n--- REDIS ---")
    print(f"  sop_candidates keys:  {len(redis_snap['candidates'])}")
    for key, info in redis_snap["candidates"].items():
        print(f"    {key}  count={info['count']} ttl={_format_ttl(info['ttl'])}")
        for tid, entry in list(info["entries"].items())[:3]:
            tools = entry.get("tool_names", [])
            user_msg = (entry.get("user_msg") or "")[:50]
            print(f"      [{tid[:20]}] tools={tools} user_msg={user_msg!r}")
        if info["count"] > 3:
            print(f"      ... +{info['count'] - 3} more")

    print(f"  sop_extracting keys:  {len(redis_snap['extracting'])} (active locks)")
    for key, info in redis_snap["extracting"].items():
        print(f"    {key}  ttl={_format_ttl(info['ttl'])}")

    print(f"  sop_ratelimit keys:   {len(redis_snap['ratelimit'])}")
    for key, info in redis_snap["ratelimit"].items():
        print(f"    {key}  ttl={_format_ttl(info['ttl'])}")

    print("\n--- SQLITE ---")
    print(f"  DBs scanned:          {len(sqlite_snap['dbs_scanned'])}")
    print(f"  Total auto_sop rows:  {sqlite_snap['auto_sop_total']}")
    for db, info in sqlite_snap["auto_sop_by_db"].items():
        if "auto_sop_count" in info and info["auto_sop_count"] > 0:
            print(f"    {db}: {info['auto_sop_count']}")
        elif "error" in info:
            print(f"    {db}: ERROR {info['error']}")

    if sqlite_snap["recent_auto_sop"]:
        print("\n  Recent auto_sop (top 10):")
        for r in sqlite_snap["recent_auto_sop"]:
            ts = time.strftime("%H:%M:%S", time.localtime(r["created_at"]))
            print(f"    [{ts}] id={r['id'][:8]} src={r['source_session_id'][:32]}")
            preview = r["content_preview"].replace("\n", " | ")
            print(f"      preview: {preview!r}")


def _diff_snapshots(prev: dict, curr: dict) -> None:
    print("\n" + "=" * 70)
    print("DIFF (vs baseline)")
    print("=" * 70)

    pr = prev["redis"]
    cr = curr["redis"]
    ps = prev["sqlite"]
    cs = curr["sqlite"]

    new_cand = set(cr["candidates"]) - set(pr["candidates"])
    gone_cand = set(pr["candidates"]) - set(cr["candidates"])
    print("\n--- REDIS DELTAS ---")
    if new_cand:
        print(f"  ➕ NEW candidates keys ({len(new_cand)}):")
        for k in sorted(new_cand):
            info = cr["candidates"][k]
            print(f"    {k}  count={info['count']}")
    if gone_cand:
        print(f"  ➖ DELETED candidates keys ({len(gone_cand)}) (likely consumed by extraction):")
        for k in sorted(gone_cand):
            info = pr["candidates"][k]
            print(f"    {k}  was count={info['count']}")
    if not new_cand and not gone_cand:
        print("  (no candidate-key changes)")

    new_lock = set(cr["extracting"]) - set(pr["extracting"])
    gone_lock = set(pr["extracting"]) - set(cr["extracting"])
    if new_lock:
        print(f"  🔒 NEW extraction locks ({len(new_lock)}):")
        for k in sorted(new_lock):
            print(f"    {k}")
    if gone_lock:
        print(f"  🔓 RELEASED locks ({len(gone_lock)}):")
        for k in sorted(gone_lock):
            print(f"    {k}")

    new_rl = set(cr["ratelimit"]) - set(pr["ratelimit"])
    if new_rl:
        print(f"  ⏱  NEW rate-limit cooldowns ({len(new_rl)}):")
        for k in sorted(new_rl):
            info = cr["ratelimit"][k]
            print(f"    {k}  ttl={_format_ttl(info['ttl'])}")

    print("\n--- SQLITE DELTAS ---")
    delta_total = cs["auto_sop_total"] - ps["auto_sop_total"]
    if delta_total > 0:
        print(f"  ➕ NEW auto_sop rows: +{delta_total}")
        prev_ids = {r["id"] for r in ps["recent_auto_sop"]}
        new_rows = [r for r in cs["recent_auto_sop"] if r["id"] not in prev_ids]
        for r in new_rows:
            ts = time.strftime("%H:%M:%S", time.localtime(r["created_at"]))
            print(f"    [{ts}] id={r['id'][:8]} src={r['source_session_id'][:32]}")
            preview = r["content_preview"].replace("\n", " | ")
            print(f"      {preview!r}")
    elif delta_total == 0:
        print("  (no new auto_sop rows)")
    else:
        print(f"  ⚠️  auto_sop count DECREASED by {-delta_total} (unexpected!)")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", action="store_true",
                        help="Save current state as baseline")
    parser.add_argument("--diff", action="store_true",
                        help="Diff current state against saved baseline")
    args = parser.parse_args()

    settings = load_settings()
    memory_base = Path(settings.memory.base_dir).expanduser()

    print(f"Redis:       {settings.redis.host}:{settings.redis.port}  "
          f"prefix={settings.redis.key_prefix!r}")
    print(f"Memory dir:  {memory_base}")
    print(f"Evolution:   enabled={settings.evolution.enabled}  "
          f"min_tool_calls={settings.evolution.min_tool_calls_for_extraction}  "
          f"max_sops={settings.evolution.max_sops_per_extraction}")

    redis_snap = await _snapshot_redis(settings)
    sqlite_snap = _snapshot_sqlite(memory_base)
    current = {
        "ts": time.time(),
        "redis": redis_snap,
        "sqlite": sqlite_snap,
    }

    _print_snapshot(redis_snap, sqlite_snap)

    if args.baseline:
        BASELINE_FILE.write_text(json.dumps(current, default=str, indent=2))
        print(f"\n✅ Baseline saved to {BASELINE_FILE}")
        return

    if args.diff:
        if not BASELINE_FILE.exists():
            print(f"\n⚠️  No baseline at {BASELINE_FILE}. "
                  f"Run with --baseline first.", file=sys.stderr)
            sys.exit(1)
        baseline = json.loads(BASELINE_FILE.read_text())
        baseline_ts = time.strftime(
            "%H:%M:%S", time.localtime(baseline["ts"])
        )
        print(f"\n(baseline taken at {baseline_ts})")
        _diff_snapshots(baseline, current)


if __name__ == "__main__":
    asyncio.run(main())
