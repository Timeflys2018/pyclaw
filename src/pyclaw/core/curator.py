"""Curator background loop — archives stale memory entries via SETNX distributed lock."""

from __future__ import annotations

import asyncio
import json as _json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import apsw

from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer

logger = logging.getLogger(__name__)

CURATOR_LOCK_KEY = "pyclaw:curator:lock"
CURATOR_LAST_RUN_KEY = "pyclaw:curator:last_run_at"
CURATOR_LLM_REVIEW_KEY = "pyclaw:curator:llm_review_last_run_at"
SCAN_CONCURRENCY = 10

REVIEW_PROMPT_TEMPLATE = """\
你是一个 SOP 质量审查员。审查以下自动生成的 SOPs，对每条给出决策：

可用决策: {actions}
- keep: 质量好，继续保留
- promote: 高频使用 + 高质量，推荐升级为正式 Skill（忽略 use_count 阈值）
- archive: 质量差或内容有害，归档

审查标准：
1. 是否是 CLASS-LEVEL 通用过程（非 instance-specific）
2. 步骤是否清晰可执行
3. 是否有安全风险
4. 内容是否过时或错误

以下是待审查的 SOPs:

{entries}

输出 JSON 数组: [{{"id": "...", "decision": "keep|promote|archive", "reason": "..."}}]
仅输出 JSON，无其他文字。
"""


@dataclass
class CuratorReport:
    total_scanned: int = 0
    total_archived: int = 0
    total_graduated: int = 0
    errors: list[str] = field(default_factory=list)


async def create_curator_loop(
    settings: Any,
    memory_base_dir: Path,
    redis_client: Any,
    l1_index: Any,
    workspace_base_dir: Path | None = None,
    llm_client: Any = None,
) -> None:

    existing = await redis_client.get(CURATOR_LAST_RUN_KEY)
    if existing is None:
        await redis_client.set(CURATOR_LAST_RUN_KEY, str(time.time()))

    try:
        while True:
            await asyncio.sleep(settings.check_interval_seconds)

            raw_last_run = await redis_client.get(CURATOR_LAST_RUN_KEY)
            if raw_last_run is not None:
                try:
                    last_run_at = float(raw_last_run)
                except (ValueError, TypeError):
                    last_run_at = 0.0
                if time.time() - last_run_at < settings.interval_seconds:
                    continue

            acquired = await redis_client.set(
                CURATOR_LOCK_KEY,
                "1",
                ex=settings.interval_seconds,
                nx=True,
            )
            if not acquired:
                continue

            try:
                report = await run_curator_scan(
                    memory_base_dir=memory_base_dir,
                    archive_days=settings.archive_after_days,
                    l1_index=l1_index,
                    workspace_base_dir=workspace_base_dir,
                    settings=settings,
                )
                await redis_client.set(CURATOR_LAST_RUN_KEY, str(time.time()))
                _log_fn = logger.info if (report.total_archived > 0 or report.total_graduated > 0) else logger.debug
                _log_fn(
                    "Curator scan complete: scanned=%d archived=%d graduated=%d errors=%d",
                    report.total_scanned,
                    report.total_archived,
                    report.total_graduated,
                    len(report.errors),
                )
                if report.errors:
                    for err in report.errors[:5]:
                        logger.warning("Curator scan error: %s", err)
            finally:
                try:
                    await redis_client.delete(CURATOR_LOCK_KEY)
                except Exception:
                    logger.debug("Curator lock release failed", exc_info=True)

            if llm_client and workspace_base_dir and await should_run_llm_review(settings, redis_client):
                for db_file in sorted(memory_base_dir.glob("*.db")):
                    try:
                        reviewed = await run_llm_review(
                            db_file=db_file,
                            settings=settings,
                            redis_client=redis_client,
                            llm_client=llm_client,
                            l1_index=l1_index,
                            workspace_base_dir=workspace_base_dir,
                        )
                        if reviewed > 0:
                            logger.info("LLM review: %d actions on %s", reviewed, db_file.name)
                    except Exception:
                        logger.warning("LLM review failed for %s", db_file.name, exc_info=True)
    except asyncio.CancelledError:
        return


async def run_curator_scan(
    memory_base_dir: Path,
    archive_days: int,
    l1_index: Any,
    workspace_base_dir: Path | None = None,
    settings: Any = None,
) -> CuratorReport:

    db_files = sorted(
        f
        for f in memory_base_dir.glob("*.db")
        if not f.name.endswith(("-wal", "-shm"))
    )

    report = CuratorReport(total_scanned=len(db_files))
    semaphore = asyncio.Semaphore(SCAN_CONCURRENCY)

    async def _bounded_scan(db_file: Path) -> tuple[int, int]:
        async with semaphore:
            return await _scan_single_db(db_file, archive_days, l1_index, workspace_base_dir=workspace_base_dir, settings=settings)

    results = await asyncio.gather(
        *[_bounded_scan(f) for f in db_files],
        return_exceptions=True,
    )

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            report.errors.append(f"{db_files[i].name}: {result!r}")
        elif isinstance(result, tuple):
            report.total_archived += result[0]
            report.total_graduated += result[1]

    return report


async def _scan_single_db(
    db_file: Path,
    archive_days: int,
    l1_index: Any,
    workspace_base_dir: Path | None = None,
    settings: Any = None,
) -> tuple[int, int]:

    threshold = time.time() - archive_days * 86400

    def _do_scan() -> list[tuple[str, str]]:
        conn = apsw.Connection(str(db_file))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            register_jieba_tokenizer(conn)

            cursor = conn.execute(
                (
                    "SELECT id, session_key FROM procedures "
                    "WHERE status='active' "
                    "AND COALESCE(last_used_at, created_at) < ?"
                ),
                (threshold,),
            )
            stale_rows = cursor.fetchall()

            if not stale_rows:
                return []

            ids = [row[0] for row in stale_rows]
            now = time.time()
            reason = f"curator:{archive_days}d_unused"
            placeholders = ",".join("?" * len(ids))
            conn.execute(
                (
                    f"UPDATE procedures SET status='archived', "  # noqa: S608
                    f"archived_at=?, archive_reason=? "
                    f"WHERE id IN ({placeholders})"
                ),
                [now, reason, *ids],
            )

            return [(str(row[0]), str(row[1])) for row in stale_rows]
        finally:
            conn.close()

    archived_rows = await asyncio.to_thread(_do_scan)

    for entry_id, session_key in archived_rows:
        try:
            await l1_index.index_remove(session_key, entry_id)
        except Exception:
            pass

    graduated_count = 0
    if settings and getattr(settings, "graduation_enabled", False) and workspace_base_dir:
        threshold_time = time.time()
        min_use = getattr(settings, "promotion_min_use_count", 5)
        min_days_ts = threshold_time - getattr(settings, "promotion_min_days", 7) * 86400

        def _find_candidates() -> list[tuple[str, str, str]]:
            conn = apsw.Connection(str(db_file))
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                register_jieba_tokenizer(conn)
                rows = list(conn.execute(
                    "SELECT id, session_key, content FROM procedures "
                    "WHERE type='auto_sop' AND status='active' "
                    "AND use_count >= ? AND created_at <= ?",
                    (min_use, min_days_ts),
                ))
                return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]
            finally:
                conn.close()

        candidates = await asyncio.to_thread(_find_candidates)

        if candidates:
            grad_conn = apsw.Connection(str(db_file))
            try:
                grad_conn.execute("PRAGMA journal_mode=WAL")
                register_jieba_tokenizer(grad_conn)

                for entry_id, session_key, content in candidates:
                    from pyclaw.core.skill_graduation import graduate_single_sop

                    grad_mode = getattr(settings, "graduation_mode", "template")
                    success, skill_path = graduate_single_sop(
                        entry_id=entry_id,
                        content=content,
                        session_key=session_key,
                        workspace_base_dir=workspace_base_dir,
                        mode=grad_mode,
                    )

                    if success:
                        grad_conn.execute(
                            "UPDATE procedures SET status='graduated' WHERE id=?",
                            (entry_id,),
                        )
                        if l1_index is not None:
                            try:
                                await l1_index.index_remove(session_key, entry_id)
                            except Exception:
                                pass
                        graduated_count += 1
                        logger.info("Graduated SOP %s → %s", entry_id[:8], skill_path)
            finally:
                grad_conn.close()

    return len(archived_rows), graduated_count


# ---------------------------------------------------------------------------
# LLM Review
# ---------------------------------------------------------------------------


@dataclass
class ReviewDecision:
    id: str
    decision: str
    reason: str


async def should_run_llm_review(
    settings: Any, redis_client: Any
) -> bool:
    if not settings.llm_review_enabled:
        return False

    last_run_raw = await redis_client.get(CURATOR_LLM_REVIEW_KEY)
    if last_run_raw is not None:
        try:
            last_run = float(last_run_raw)
            if time.time() - last_run < settings.llm_review_interval_seconds:
                return False
        except (ValueError, TypeError):
            pass

    return True


def _open_review_db(db_file: Path) -> apsw.Connection:
    conn = apsw.Connection(str(db_file))
    conn.execute("PRAGMA journal_mode=WAL")
    register_jieba_tokenizer(conn)
    return conn


async def run_llm_review(
    db_file: Path,
    settings: Any,
    redis_client: Any,
    llm_client: Any,
    l1_index: Any,
    workspace_base_dir: Path,
) -> int:
    conn = await asyncio.to_thread(_open_review_db, db_file)

    try:
        def _get_entries() -> list[tuple[str, str, str, int]]:
            rows = list(conn.execute(
                "SELECT id, session_key, content, use_count FROM procedures "
                "WHERE type='auto_sop' AND status='active' "
                "ORDER BY use_count DESC LIMIT ?",
                (settings.llm_review_max_batch,),
            ))
            return [(str(r[0]), str(r[1]), str(r[2]), int(r[3] or 0)) for r in rows]

        entries = await asyncio.to_thread(_get_entries)
        if not entries:
            return 0

        entries_text = "\n".join(
            f"[id={eid}, use_count={uc}]\n{content[:200]}\n---"
            for eid, _sk, content, uc in entries
        )
        actions_text = "/".join(settings.llm_review_actions)

        prompt = REVIEW_PROMPT_TEMPLATE.format(
            actions=actions_text, entries=entries_text
        )

        model = settings.llm_review_model
        try:
            response = await asyncio.wait_for(
                llm_client.complete(
                    messages=[{"role": "user", "content": prompt}],
                    model=model,
                ),
                timeout=30.0,
            )
            llm_output = response.text
        except Exception as exc:
            logger.warning("LLM review call failed: %s", exc)
            return 0

        decisions = _parse_review_decisions(llm_output, settings.llm_review_actions)
        action_count = 0
        entry_map = {eid: (sk, content) for eid, sk, content, _uc in entries}

        for decision in decisions:
            if decision.id not in entry_map:
                continue
            session_key, content = entry_map[decision.id]

            if decision.decision == "promote" and "promote" in settings.llm_review_actions:
                if not getattr(settings, "graduation_enabled", True):
                    logger.info("LLM review suggests promote for %s but graduation disabled", decision.id[:8])
                    continue

                from pyclaw.core.skill_graduation import graduate_single_sop

                grad_mode = getattr(settings, "graduation_mode", "template")
                success, _ = graduate_single_sop(
                    entry_id=decision.id,
                    content=content,
                    session_key=session_key,
                    workspace_base_dir=workspace_base_dir,
                    mode=grad_mode,
                )
                if success:
                    eid_promote = decision.id

                    def _mark_graduated(eid: str = eid_promote) -> None:
                        conn.execute(
                            "UPDATE procedures SET status='graduated' WHERE id=?", (eid,)
                        )

                    await asyncio.to_thread(_mark_graduated)
                    if l1_index:
                        try:
                            await l1_index.index_remove(session_key, decision.id)
                        except Exception:
                            pass
                    action_count += 1
                    logger.info("LLM review promoted %s", decision.id[:8])

            elif decision.decision == "archive" and "archive" in settings.llm_review_actions:
                eid_archive = decision.id
                reason_text = f"llm_review: {decision.reason[:100]}"
                now = time.time()

                def _mark_archived(eid: str = eid_archive, reason: str = reason_text, ts: float = now) -> None:
                    conn.execute(
                        "UPDATE procedures SET status='archived', archived_at=?, archive_reason=? WHERE id=?",
                        (ts, reason, eid),
                    )

                await asyncio.to_thread(_mark_archived)
                if l1_index:
                    try:
                        await l1_index.index_remove(session_key, decision.id)
                    except Exception:
                        pass
                action_count += 1
                logger.info("LLM review archived %s: %s", decision.id[:8], decision.reason[:50])

        await redis_client.set(CURATOR_LLM_REVIEW_KEY, str(int(time.time())))

        return action_count
    finally:
        conn.close()


def _parse_review_decisions(
    llm_output: str, allowed_actions: list[str]
) -> list[ReviewDecision]:
    text = llm_output.strip()
    fence_match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)\n?\s*```", text)
    if fence_match:
        text = fence_match.group(1)

    try:
        items = _json.loads(text)
    except _json.JSONDecodeError:
        arr_match = re.search(r"\[[\s\S]*\]", text)
        if arr_match:
            try:
                items = _json.loads(arr_match.group(0))
            except _json.JSONDecodeError:
                return []
        else:
            return []

    if not isinstance(items, list):
        return []

    decisions = []
    for item in items:
        if not isinstance(item, dict):
            continue
        eid = item.get("id", "")
        decision_val = item.get("decision", "")
        reason = item.get("reason", "")
        if decision_val in allowed_actions or decision_val == "keep":
            decisions.append(ReviewDecision(id=eid, decision=decision_val, reason=reason))

    return decisions
