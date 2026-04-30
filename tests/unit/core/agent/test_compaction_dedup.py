from __future__ import annotations

from pyclaw.core.agent.compaction.dedup import (
    dedupe_duplicate_user_messages,
    normalize_for_dedup,
)


def test_normalize_lowercases_and_collapses_whitespace() -> None:
    assert normalize_for_dedup("Hello   World\n\n") == "hello world"


def test_normalize_applies_nfc() -> None:
    composed = "café"
    decomposed = "cafe\u0301"
    assert normalize_for_dedup(composed) == normalize_for_dedup(decomposed)


def test_normalize_empty_is_empty() -> None:
    assert normalize_for_dedup("") == ""


def test_short_messages_preserved() -> None:
    msgs = [
        {"role": "user", "content": "ok", "timestamp": 0.0},
        {"role": "user", "content": "ok", "timestamp": 1.0},
        {"role": "user", "content": "ok", "timestamp": 2.0},
    ]
    out = dedupe_duplicate_user_messages(msgs, window_seconds=60, min_chars=24)
    assert len(out) == 3


def test_duplicate_within_window_deduped() -> None:
    msg_text = "A" * 40
    msgs = [
        {"role": "user", "content": msg_text, "timestamp": 0.0},
        {"role": "user", "content": msg_text, "timestamp": 10.0},
        {"role": "user", "content": msg_text, "timestamp": 20.0},
    ]
    out = dedupe_duplicate_user_messages(msgs, window_seconds=60, min_chars=24)
    assert len(out) == 1


def test_duplicate_outside_window_preserved() -> None:
    msg_text = "A" * 40
    msgs = [
        {"role": "user", "content": msg_text, "timestamp": 0.0},
        {"role": "user", "content": msg_text, "timestamp": 200.0},
    ]
    out = dedupe_duplicate_user_messages(msgs, window_seconds=60, min_chars=24)
    assert len(out) == 2


def test_whitespace_normalization_deduplicates() -> None:
    a = "Hello   world with lots of context here"
    b = "hello world   with   lots  of   context here"
    msgs = [
        {"role": "user", "content": a, "timestamp": 0.0},
        {"role": "user", "content": b, "timestamp": 5.0},
    ]
    out = dedupe_duplicate_user_messages(msgs, window_seconds=60, min_chars=24)
    assert len(out) == 1


def test_non_user_messages_never_deduped() -> None:
    text = "A" * 40
    msgs = [
        {"role": "assistant", "content": text, "timestamp": 0.0},
        {"role": "assistant", "content": text, "timestamp": 5.0},
    ]
    out = dedupe_duplicate_user_messages(msgs)
    assert len(out) == 2
