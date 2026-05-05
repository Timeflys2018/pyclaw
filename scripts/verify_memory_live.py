"""
Live data-plane verification for Change 3b + 3c (memory integration).

Run this alongside a live pyclaw server while testing via Feishu/Web.
It continuously inspects Redis (L1 index, working memory, archive task markers)
and SQLite (L2/L3 facts/procedures, L4 archives) for every session.

Usage:
    # Terminal 1: start pyclaw server (with real Redis + Feishu enabled)
    .venv/bin/uvicorn pyclaw.app:create_app --factory --port 8000

    # Terminal 2: run this watcher
    .venv/bin/python scripts/verify_memory_live.py

    # Optional: watch one specific session only
    .venv/bin/python scripts/verify_memory_live.py --session-key feishu:cli_xxx:ou_yyy

    # Optional: one-shot snapshot then exit
    .venv/bin/python scripts/verify_memory_live.py --once

    # Optional: run the built-in expectation checker
    .venv/bin/python scripts/verify_memory_live.py --check T5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import redis.asyncio as aioredis

from pyclaw.infra.settings import load_settings


GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


def green(s: str) -> str:
    return f"{GREEN}{s}{RESET}"


def red(s: str) -> str:
    return f"{RED}{s}{RESET}"


def yellow(s: str) -> str:
    return f"{YELLOW}{s}{RESET}"


def cyan(s: str) -> str:
    return f"{CYAN}{s}{RESET}"


def bold(s: str) -> str:
    return f"{BOLD}{s}{RESET}"


def dim(s: str) -> str:
    return f"{DIM}{s}{RESET}"


@dataclass
class Snapshot:
    session_keys: list[str]
    working_memory: dict[str, dict[str, str]]
    l1_indices: dict[str, list[dict[str, Any]]]
    sqlite_stats: dict[str, dict[str, int]]
    sqlite_recent_facts: dict[str, list[dict[str, Any]]]
    sqlite_recent_procs: dict[str, list[dict[str, Any]]]
    sqlite_archives: dict[str, list[dict[str, Any]]]


async def fetch_session_keys(redis: aioredis.Redis, prefix: str) -> list[str]:
    l1_keys = await redis.keys(f"{prefix}memory:L1:*")
    wm_keys = await redis.keys(f"{prefix}wm:*")
    session_keys: set[str] = set()
    for k in l1_keys:
        ks = k if isinstance(k, str) else k.decode()
        session_keys.add(ks.removeprefix(f"{prefix}memory:L1:"))
    for k in wm_keys:
        ks = k if isinstance(k, str) else k.decode()
        stripped = ks.removeprefix(f"{prefix}wm:").removesuffix(":order")
        if ":s:" in stripped:
            derived = stripped.split(":s:")[0]
            session_keys.add(derived)
    return sorted(session_keys)


async def fetch_working_memory(
    redis: aioredis.Redis, prefix: str, session_key: str
) -> dict[str, str]:
    pattern = f"{prefix}wm:{session_key}:s:*"
    keys = await redis.keys(pattern)
    result: dict[str, str] = {}
    for k in keys:
        ks = k if isinstance(k, str) else k.decode()
        if ks.endswith(":order"):
            continue
        hash_data = await redis.hgetall(ks)
        short_sid = ks.split(":s:")[-1]
        for field, value in hash_data.items():
            fname = field if isinstance(field, str) else field.decode()
            vname = value if isinstance(value, str) else value.decode()
            result[f"[{short_sid[:8]}] {fname}"] = vname
    return result


async def fetch_l1_index(
    redis: aioredis.Redis, prefix: str, session_key: str
) -> list[dict[str, Any]]:
    key = f"{prefix}memory:L1:{session_key}"
    raw = await redis.hgetall(key)
    entries: list[dict[str, Any]] = []
    for _, v in raw.items():
        vs = v if isinstance(v, str) else v.decode()
        try:
            entry = json.loads(vs)
            entries.append(entry)
        except json.JSONDecodeError:
            continue
    entries.sort(key=lambda e: e.get("updated_at", 0), reverse=True)
    return entries


def session_key_to_db_path(base_dir: Path, session_key: str) -> Path:
    db_name = session_key.replace(":", "_") + ".db"
    return base_dir / db_name


def inspect_sqlite(db_path: Path) -> dict[str, Any]:
    if not db_path.is_file():
        return {
            "exists": False,
            "stats": {"facts": 0, "procedures": 0, "archives": 0},
            "recent_facts": [],
            "recent_procs": [],
            "archives": [],
        }
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        facts_count = cur.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        procs_count = cur.execute("SELECT COUNT(*) FROM procedures").fetchone()[0]
        archives_count = cur.execute("SELECT COUNT(*) FROM archives").fetchone()[0]
        recent_facts = [
            dict(r)
            for r in cur.execute(
                "SELECT id, type, content, created_at FROM facts "
                "ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
        ]
        recent_procs = [
            dict(r)
            for r in cur.execute(
                "SELECT id, type, content, status, use_count, created_at FROM procedures "
                "ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
        ]
        archives = [
            dict(r)
            for r in cur.execute(
                "SELECT id, session_id, substr(summary, 1, 80) AS summary_preview, created_at "
                "FROM archives ORDER BY created_at DESC LIMIT 5"
            ).fetchall()
        ]
        return {
            "exists": True,
            "stats": {
                "facts": facts_count,
                "procedures": procs_count,
                "archives": archives_count,
            },
            "recent_facts": recent_facts,
            "recent_procs": recent_procs,
            "archives": archives,
        }
    finally:
        conn.close()


async def collect_snapshot(
    redis: aioredis.Redis,
    redis_prefix: str,
    sqlite_base: Path,
    session_filter: str | None,
) -> Snapshot:
    session_keys = await fetch_session_keys(redis, redis_prefix)
    if session_filter:
        session_keys = [k for k in session_keys if k == session_filter]

    wm: dict[str, dict[str, str]] = {}
    l1: dict[str, list[dict[str, Any]]] = {}
    sq_stats: dict[str, dict[str, int]] = {}
    sq_facts: dict[str, list[dict[str, Any]]] = {}
    sq_procs: dict[str, list[dict[str, Any]]] = {}
    sq_archives: dict[str, list[dict[str, Any]]] = {}

    for sk in session_keys:
        wm[sk] = await fetch_working_memory(redis, redis_prefix, sk)
        l1[sk] = await fetch_l1_index(redis, redis_prefix, sk)
        sqlite_info = inspect_sqlite(session_key_to_db_path(sqlite_base, sk))
        sq_stats[sk] = sqlite_info["stats"]
        sq_facts[sk] = sqlite_info["recent_facts"]
        sq_procs[sk] = sqlite_info["recent_procs"]
        sq_archives[sk] = sqlite_info["archives"]

    return Snapshot(
        session_keys=session_keys,
        working_memory=wm,
        l1_indices=l1,
        sqlite_stats=sq_stats,
        sqlite_recent_facts=sq_facts,
        sqlite_recent_procs=sq_procs,
        sqlite_archives=sq_archives,
    )


def render_snapshot(snap: Snapshot, config_summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(bold(cyan("━" * 80)))
    lines.append(
        bold(cyan(f"  pyclaw memory live verify  |  {time.strftime('%H:%M:%S')}"))
    )
    lines.append(bold(cyan("━" * 80)))

    lines.append(
        dim(
            f"config: L2 quota={config_summary['l2_quota']} | "
            f"L3 quota={config_summary['l3_quota']} | "
            f"fts_min_chars={config_summary['fts_min_chars']} | "
            f"archive_sim≥{config_summary['archive_similarity']} | "
            f"archive_min_results={config_summary['archive_min_results']} | "
            f"base={config_summary['base_dir']}"
        )
    )
    lines.append("")

    if not snap.session_keys:
        lines.append(yellow("  (no sessions found - send a message in Feishu/Web to begin)"))
        return "\n".join(lines)

    for sk in snap.session_keys:
        lines.append(bold(f"┌─ session_key: {cyan(sk)}"))

        wm = snap.working_memory.get(sk, {})
        if wm:
            lines.append(f"│  {green('working_memory')} ({len(wm)} entries):")
            for k, v in list(wm.items())[:5]:
                lines.append(f"│    • {k} = {v[:60]}")
            if len(wm) > 5:
                lines.append(f"│    {dim(f'… +{len(wm) - 5} more')}")
        else:
            lines.append(f"│  {dim('working_memory: (empty)')}")

        l1 = snap.l1_indices.get(sk, [])
        if l1:
            lines.append(f"│  {green('L1 index (Redis)')} ({len(l1)} entries):")
            for e in l1[:5]:
                layer = e.get("layer", "?")
                etype = e.get("type", "?")
                content = e.get("content", "")[:55]
                lines.append(f"│    • [{layer}/{etype}] {content}")
            if len(l1) > 5:
                lines.append(f"│    {dim(f'… +{len(l1) - 5} more')}")
        else:
            lines.append(f"│  {dim('L1 index: (empty)')}")

        stats = snap.sqlite_stats.get(sk, {})
        if stats:
            facts_str = f"facts={stats.get('facts', 0)}"
            procs_str = f"procedures={stats.get('procedures', 0)}"
            arch_str = f"archives={stats.get('archives', 0)}"
            lines.append(
                f"│  {green('SQLite')}: {facts_str} | {procs_str} | {arch_str}"
            )

        facts = snap.sqlite_recent_facts.get(sk, [])
        if facts:
            lines.append(f"│    {dim('recent L2 facts:')}")
            for f in facts[:3]:
                lines.append(
                    f"│      - [{f['type']}] {f['content'][:60]}"
                )

        procs = snap.sqlite_recent_procs.get(sk, [])
        if procs:
            lines.append(f"│    {dim('recent L3 procedures:')}")
            for p in procs[:3]:
                lines.append(
                    f"│      - [{p['type']}/{p.get('status', '?')}] {p['content'][:55]}"
                )

        archives = snap.sqlite_archives.get(sk, [])
        if archives:
            lines.append(f"│    {dim('recent L4 archives:')}")
            for a in archives[:3]:
                ts = time.strftime("%H:%M:%S", time.localtime(a.get("created_at", 0)))
                sid_short = a["session_id"].split(":s:")[-1][:8]
                lines.append(
                    f"│      - {ts} [sid={sid_short}] {a['summary_preview']}"
                )

        lines.append(bold("└─"))
        lines.append("")

    return "\n".join(lines)


def check_expectation(snap: Snapshot, tid: str) -> tuple[bool, list[str]]:
    msgs: list[str] = []
    ok = True

    if tid == "T2":
        total_wm = sum(len(wm) for wm in snap.working_memory.values())
        if total_wm > 0:
            msgs.append(green(f"✓ T2: working_memory has {total_wm} entries"))
        else:
            msgs.append(red("✗ T2: no working_memory entries found"))
            ok = False
    elif tid == "T3" or tid == "T4":
        total_l1 = sum(len(l1) for l1 in snap.l1_indices.values())
        total_facts = sum(s.get("facts", 0) for s in snap.sqlite_stats.values())
        if total_l1 > 0 and total_facts > 0:
            msgs.append(green(f"✓ T3/T4: L1 has {total_l1}, SQLite facts has {total_facts}"))
        else:
            msgs.append(red(f"✗ T3/T4: L1={total_l1}, SQLite facts={total_facts} (expect both > 0)"))
            ok = False
    elif tid == "T5":
        total_facts = sum(s.get("facts", 0) for s in snap.sqlite_stats.values())
        total_procs = sum(s.get("procedures", 0) for s in snap.sqlite_stats.values())
        if total_facts >= 3:
            msgs.append(green(f"✓ T5 L2: SQLite facts has {total_facts} (≥3)"))
        else:
            msgs.append(red(f"✗ T5 L2: only {total_facts} facts (expected ≥3)"))
            ok = False
        if total_procs >= 2:
            msgs.append(green(f"✓ T5 L3: SQLite procedures has {total_procs} (≥2)"))
        else:
            msgs.append(red(f"✗ T5 L3: only {total_procs} procedures (expected ≥2)"))
            ok = False
    elif tid == "T6":
        total_archives = sum(s.get("archives", 0) for s in snap.sqlite_stats.values())
        if total_archives > 0:
            msgs.append(green(f"✓ T6: {total_archives} archive(s) written to L4"))
        else:
            msgs.append(red("✗ T6: no archives in SQLite (expected ≥1 after /new on non-empty session)"))
            ok = False
    else:
        msgs.append(yellow(f"Unknown check ID: {tid}"))
        ok = False
    return ok, msgs


def build_config_summary() -> dict[str, Any]:
    s = load_settings()
    m = s.memory
    return {
        "l2_quota": m.search_l2_quota,
        "l3_quota": m.search_l3_quota,
        "fts_min_chars": m.search_fts_min_query_chars,
        "archive_similarity": m.archive_min_similarity,
        "archive_min_results": m.archive_min_results,
        "base_dir": str(Path(m.base_dir).expanduser()),
    }


async def build_redis_client() -> tuple[aioredis.Redis, str]:
    s = load_settings()
    url = s.redis.build_url()
    client = aioredis.from_url(url, decode_responses=True)
    await client.ping()
    return client, s.redis.key_prefix


async def run_once(session_filter: str | None, check_id: str | None) -> int:
    try:
        client, prefix = await build_redis_client()
    except Exception as exc:
        print(red(f"✗ Redis connection failed: {exc}"))
        print(dim("Hint: check configs/pyclaw.json redis section"))
        return 2

    config = build_config_summary()
    sqlite_base = Path(config["base_dir"])

    try:
        snap = await collect_snapshot(client, prefix, sqlite_base, session_filter)
        print(render_snapshot(snap, config))

        if check_id:
            print(bold(cyan("─── expectation check ───")))
            ok, msgs = check_expectation(snap, check_id)
            for m in msgs:
                print(f"  {m}")
            return 0 if ok else 1

        return 0
    finally:
        await client.aclose()


async def run_watch(interval: float, session_filter: str | None) -> int:
    try:
        client, prefix = await build_redis_client()
    except Exception as exc:
        print(red(f"✗ Redis connection failed: {exc}"))
        return 2

    config = build_config_summary()
    sqlite_base = Path(config["base_dir"])

    print(dim(f"Watching every {interval}s. Ctrl+C to exit."))
    print()

    prev_hash: str | None = None
    try:
        while True:
            snap = await collect_snapshot(client, prefix, sqlite_base, session_filter)
            rendered = render_snapshot(snap, config)

            sig = (
                f"{len(snap.session_keys)}|"
                + "|".join(
                    f"{sk}:wm{len(snap.working_memory[sk])}"
                    f":l1{len(snap.l1_indices[sk])}"
                    f":f{snap.sqlite_stats[sk].get('facts', 0)}"
                    f":p{snap.sqlite_stats[sk].get('procedures', 0)}"
                    f":a{snap.sqlite_stats[sk].get('archives', 0)}"
                    for sk in snap.session_keys
                )
            )

            if sig != prev_hash:
                print("\033[2J\033[H", end="")
                print(rendered)
                if prev_hash is not None:
                    print(yellow(f"  ⟳ state changed @ {time.strftime('%H:%M:%S')}"))
                prev_hash = sig

            await asyncio.sleep(interval)
    except KeyboardInterrupt:
        print()
        print(dim("stopped"))
        return 0
    finally:
        await client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Live Redis + SQLite data-plane verification for pyclaw memory.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Watch all sessions (default, refresh on change):
    .venv/bin/python scripts/verify_memory_live.py

  Watch one specific session:
    .venv/bin/python scripts/verify_memory_live.py --session-key feishu:cli_xxx:ou_yyy

  One-shot snapshot:
    .venv/bin/python scripts/verify_memory_live.py --once

  Verify a specific expectation (T2/T3/T4/T5/T6):
    .venv/bin/python scripts/verify_memory_live.py --check T5
""",
    )
    parser.add_argument("--interval", type=float, default=2.0, help="Watch interval seconds")
    parser.add_argument("--session-key", type=str, default=None, help="Filter to one session_key")
    parser.add_argument("--once", action="store_true", help="Single snapshot then exit")
    parser.add_argument("--check", type=str, default=None, metavar="TID", help="Run expectation check (T2/T3/T4/T5/T6)")
    args = parser.parse_args()

    if args.once or args.check:
        return asyncio.run(run_once(args.session_key, args.check))
    return asyncio.run(run_watch(args.interval, args.session_key))


if __name__ == "__main__":
    sys.exit(main())
