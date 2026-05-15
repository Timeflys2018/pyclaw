from __future__ import annotations

import json
import time
from dataclasses import FrozenInstanceError, dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import apsw
import pytest

from pyclaw.core.curator import (
    ReviewDecision,
    ReviewOutcome,
    _parse_review_decisions,
    run_llm_review,
    should_run_llm_review,
)
from pyclaw.core.curator_state import CuratorStateStore
from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer


@dataclass
class FakeSettings:
    llm_review_enabled: bool = False
    llm_review_model: str | None = "gpt-4o-mini"
    llm_review_interval_seconds: int = 1209600
    llm_review_actions: list[str] | None = None
    llm_review_max_batch: int = 20

    def __post_init__(self) -> None:
        if self.llm_review_actions is None:
            self.llm_review_actions = ["promote", "archive"]


def _create_test_db(path: Path, entries: list[dict]) -> None:
    conn = apsw.Connection(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    register_jieba_tokenizer(conn)
    conn.execute(
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
            status TEXT DEFAULT 'active',
            archived_at REAL,
            archive_reason TEXT
        )"""
    )
    for e in entries:
        conn.execute(
            "INSERT INTO procedures (id, session_key, type, content, source_session_id, "
            "created_at, updated_at, last_used_at, use_count, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e["id"],
                e["session_key"],
                e.get("type", "auto_sop"),
                e["content"],
                e.get("source_session_id", "ses_1"),
                e["created_at"],
                e["updated_at"],
                e.get("last_used_at"),
                e.get("use_count", 0),
                e.get("status", "active"),
            ),
        )
    conn.close()


class TestReviewOutcome:
    """B1: ReviewOutcome dataclass is frozen and exposes per-action counts."""

    def test_constructs_with_all_fields(self, tmp_path: Path) -> None:
        db_file = tmp_path / "x.db"
        outcome = ReviewOutcome(
            db_file=db_file,
            entries_reviewed=5,
            promoted_count=1,
            archived_count=2,
            failed_count=0,
        )
        assert outcome.db_file == db_file
        assert outcome.entries_reviewed == 5
        assert outcome.promoted_count == 1
        assert outcome.archived_count == 2
        assert outcome.failed_count == 0

    def test_is_frozen(self, tmp_path: Path) -> None:
        outcome = ReviewOutcome(
            db_file=tmp_path / "x.db",
            entries_reviewed=0,
            promoted_count=0,
            archived_count=0,
            failed_count=0,
        )
        with pytest.raises(FrozenInstanceError):
            outcome.promoted_count = 99  # pyright: ignore[reportAttributeAccessIssue]

    def test_total_actions_sums_promoted_plus_archived(self, tmp_path: Path) -> None:
        outcome = ReviewOutcome(
            db_file=tmp_path / "x.db",
            entries_reviewed=10,
            promoted_count=3,
            archived_count=4,
            failed_count=2,
        )
        assert outcome.total_actions == 7


class TestShouldRunLlmReview:
    @pytest.mark.asyncio
    async def test_disabled_returns_false(self) -> None:
        settings = FakeSettings(llm_review_enabled=False)
        redis = AsyncMock()
        store = CuratorStateStore(redis)
        assert await should_run_llm_review(settings, store) is False
        redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_enabled_no_last_run_returns_true(self) -> None:
        settings = FakeSettings(llm_review_enabled=True)
        redis = AsyncMock()
        redis.get.return_value = None
        store = CuratorStateStore(redis)
        assert await should_run_llm_review(settings, store) is True

    @pytest.mark.asyncio
    async def test_enabled_recent_run_returns_false(self) -> None:
        settings = FakeSettings(llm_review_enabled=True, llm_review_interval_seconds=3600)
        redis = AsyncMock()
        redis.get.return_value = str(time.time() - 100)
        store = CuratorStateStore(redis)
        assert await should_run_llm_review(settings, store) is False

    @pytest.mark.asyncio
    async def test_enabled_old_run_returns_true(self) -> None:
        settings = FakeSettings(llm_review_enabled=True, llm_review_interval_seconds=3600)
        redis = AsyncMock()
        redis.get.return_value = str(time.time() - 7200)
        store = CuratorStateStore(redis)
        assert await should_run_llm_review(settings, store) is True

    @pytest.mark.asyncio
    async def test_corrupted_last_run_returns_true(self) -> None:
        settings = FakeSettings(llm_review_enabled=True)
        redis = AsyncMock()
        redis.get.return_value = "not-a-number"
        store = CuratorStateStore(redis)
        assert await should_run_llm_review(settings, store) is True


class TestParseReviewDecisions:
    def test_valid_json(self) -> None:
        output = json.dumps(
            [
                {"id": "abc123", "decision": "promote", "reason": "good quality"},
                {"id": "def456", "decision": "archive", "reason": "low quality"},
            ]
        )
        result = _parse_review_decisions(output, ["promote", "archive"])
        assert len(result) == 2
        assert result[0] == ReviewDecision(id="abc123", decision="promote", reason="good quality")
        assert result[1] == ReviewDecision(id="def456", decision="archive", reason="low quality")

    def test_fenced_json(self) -> None:
        output = (
            "```json\n"
            + json.dumps(
                [
                    {"id": "abc", "decision": "keep", "reason": "fine"},
                ]
            )
            + "\n```"
        )
        result = _parse_review_decisions(output, ["promote", "archive"])
        assert len(result) == 1
        assert result[0].decision == "keep"

    def test_invalid_json_returns_empty(self) -> None:
        result = _parse_review_decisions("this is not json at all", ["promote"])
        assert result == []

    def test_filters_unknown_actions(self) -> None:
        output = json.dumps(
            [
                {"id": "a", "decision": "promote", "reason": "ok"},
                {"id": "b", "decision": "patch", "reason": "should be filtered"},
                {"id": "c", "decision": "keep", "reason": "always allowed"},
            ]
        )
        result = _parse_review_decisions(output, ["promote"])
        assert len(result) == 2
        assert result[0].decision == "promote"
        assert result[1].decision == "keep"

    def test_embedded_array_in_text(self) -> None:
        output = 'Here is the result:\n[{"id": "x", "decision": "archive", "reason": "bad"}]\nDone.'
        result = _parse_review_decisions(output, ["archive"])
        assert len(result) == 1
        assert result[0].decision == "archive"

    def test_non_list_returns_empty(self) -> None:
        result = _parse_review_decisions('{"id": "a"}', ["promote"])
        assert result == []

    def test_non_dict_items_skipped(self) -> None:
        output = json.dumps(["not a dict", {"id": "a", "decision": "promote", "reason": "ok"}])
        result = _parse_review_decisions(output, ["promote"])
        assert len(result) == 1


class TestRunLlmReview:
    @pytest.mark.asyncio
    async def test_empty_db_returns_zero(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test.db"
        _create_test_db(db_file, [])
        settings = FakeSettings(llm_review_enabled=True)
        llm = AsyncMock()
        l1 = AsyncMock()

        result = await run_llm_review(db_file, settings, llm, l1, tmp_path)
        assert isinstance(result, ReviewOutcome)
        assert result.entries_reviewed == 0
        assert result.total_actions == 0
        llm.complete.assert_not_called()

    @pytest.mark.asyncio
    async def test_archive_decision_updates_db(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test.db"
        now = time.time()
        _create_test_db(
            db_file,
            [
                {
                    "id": "entry_001",
                    "session_key": "ws:default",
                    "content": "test-sop\ndesc\nstep 1\nstep 2",
                    "created_at": now - 86400,
                    "updated_at": now - 86400,
                    "use_count": 2,
                },
            ],
        )

        settings = FakeSettings(llm_review_enabled=True)

        @dataclass
        class FakeLLMResponse:
            text: str

        llm = AsyncMock()
        llm.complete.return_value = FakeLLMResponse(
            text=json.dumps([{"id": "entry_001", "decision": "archive", "reason": "low quality"}])
        )
        l1 = AsyncMock()

        result = await run_llm_review(db_file, settings, llm, l1, tmp_path)
        assert result.archived_count == 1
        assert result.promoted_count == 0
        assert result.total_actions == 1

        conn = apsw.Connection(str(db_file))
        row = list(
            conn.execute("SELECT status, archive_reason FROM procedures WHERE id='entry_001'")
        )
        conn.close()
        assert row[0][0] == "archived"
        assert "llm_review:" in row[0][1]

        l1.index_remove.assert_called_once_with("ws:default", "entry_001")

    @pytest.mark.asyncio
    async def test_promote_decision_triggers_graduation(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test.db"
        now = time.time()
        _create_test_db(
            db_file,
            [
                {
                    "id": "entry_002",
                    "session_key": "ws:default",
                    "content": "my-skill\nA good skill\nStep 1: do something\nStep 2: finish",
                    "created_at": now - 86400,
                    "updated_at": now - 86400,
                    "use_count": 10,
                },
            ],
        )

        settings = FakeSettings(llm_review_enabled=True)

        @dataclass
        class FakeLLMResponse:
            text: str

        llm = AsyncMock()
        llm.complete.return_value = FakeLLMResponse(
            text=json.dumps([{"id": "entry_002", "decision": "promote", "reason": "high quality"}])
        )
        l1 = AsyncMock()

        with patch(
            "pyclaw.core.skill_graduation.graduate_single_sop", return_value=(True, "/tmp/skill")
        ) as mock_grad:
            result = await run_llm_review(db_file, settings, llm, l1, tmp_path)

        assert result.promoted_count == 1
        assert result.archived_count == 0
        assert result.total_actions == 1
        mock_grad.assert_called_once_with(
            entry_id="entry_002",
            content="my-skill\nA good skill\nStep 1: do something\nStep 2: finish",
            session_key="ws:default",
            workspace_base_dir=tmp_path,
            mode="template",
        )

        conn = apsw.Connection(str(db_file))
        row = list(conn.execute("SELECT status FROM procedures WHERE id='entry_002'"))
        conn.close()
        assert row[0][0] == "graduated"

    @pytest.mark.asyncio
    async def test_llm_call_failure_returns_zero(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test.db"
        now = time.time()
        _create_test_db(
            db_file,
            [
                {
                    "id": "entry_003",
                    "session_key": "ws:default",
                    "content": "test\ndesc\nsteps",
                    "created_at": now,
                    "updated_at": now,
                    "use_count": 1,
                },
            ],
        )

        settings = FakeSettings(llm_review_enabled=True)
        llm = AsyncMock()
        llm.complete.side_effect = RuntimeError("API down")
        l1 = AsyncMock()

        result = await run_llm_review(db_file, settings, llm, l1, tmp_path)
        assert result.total_actions == 0
        assert result.failed_count == 1

    @pytest.mark.asyncio
    async def test_keep_decision_no_action(self, tmp_path: Path) -> None:
        db_file = tmp_path / "test.db"
        now = time.time()
        _create_test_db(
            db_file,
            [
                {
                    "id": "entry_004",
                    "session_key": "ws:default",
                    "content": "good-sop\nfine desc\nstep 1",
                    "created_at": now,
                    "updated_at": now,
                    "use_count": 3,
                },
            ],
        )

        settings = FakeSettings(llm_review_enabled=True)

        @dataclass
        class FakeLLMResponse:
            text: str

        llm = AsyncMock()
        llm.complete.return_value = FakeLLMResponse(
            text=json.dumps([{"id": "entry_004", "decision": "keep", "reason": "fine"}])
        )
        l1 = AsyncMock()

        result = await run_llm_review(db_file, settings, llm, l1, tmp_path)
        assert result.total_actions == 0
        assert result.entries_reviewed == 1

        conn = apsw.Connection(str(db_file))
        row = list(conn.execute("SELECT status FROM procedures WHERE id='entry_004'"))
        conn.close()
        assert row[0][0] == "active"
