from __future__ import annotations

from pyclaw.core.agent.compaction_reasons import (
    ALL_REASON_CODES,
    classify_compaction_reason,
)


def test_all_10_plus_reason_codes_present() -> None:
    assert len(ALL_REASON_CODES) >= 10
    assert "compacted" in ALL_REASON_CODES
    assert "no_compactable_entries" in ALL_REASON_CODES
    assert "below_threshold" in ALL_REASON_CODES
    assert "already_compacted_recently" in ALL_REASON_CODES
    assert "live_context_still_exceeds_target" in ALL_REASON_CODES
    assert "guard_blocked" in ALL_REASON_CODES
    assert "summary_failed" in ALL_REASON_CODES
    assert "timeout" in ALL_REASON_CODES
    assert "aborted" in ALL_REASON_CODES
    assert "unknown" in ALL_REASON_CODES


def test_classify_none_returns_unknown() -> None:
    assert classify_compaction_reason(None) == "unknown"
    assert classify_compaction_reason("") == "unknown"


def test_classify_timeout() -> None:
    assert classify_compaction_reason("summarizer timeout after 900s") == "timeout"
    assert classify_compaction_reason("request timed out") == "timeout"


def test_classify_aborted() -> None:
    assert classify_compaction_reason("user aborted run") == "aborted"
    assert classify_compaction_reason("cancel requested") == "aborted"


def test_classify_below_threshold() -> None:
    assert classify_compaction_reason("within-budget") == "below_threshold"
    assert classify_compaction_reason("below threshold") == "below_threshold"


def test_classify_no_compactable() -> None:
    assert classify_compaction_reason("no-safe-cut-point") == "no_compactable_entries"
    assert classify_compaction_reason("no compactable entries") == "no_compactable_entries"


def test_classify_summary_failed() -> None:
    assert classify_compaction_reason("summary failed: provider error") == "summary_failed"


def test_classify_compacted() -> None:
    assert classify_compaction_reason("compacted-at-42") == "compacted"
    assert classify_compaction_reason("compacted") == "compacted"


def test_classify_already_compacted() -> None:
    assert (
        classify_compaction_reason("already compacted within last 5 minutes")
        == "already_compacted_recently"
    )


def test_classify_guard_blocked() -> None:
    assert classify_compaction_reason("guard blocked by policy") == "guard_blocked"
