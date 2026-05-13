from __future__ import annotations

from pyclaw.channels.web.chat import SessionQueue


def test_last_usage_unset_returns_none() -> None:
    sq = SessionQueue()
    assert sq.get_last_usage("conv-never-ran") is None


def test_last_usage_set_and_get_roundtrip() -> None:
    sq = SessionQueue()
    usage = {"input": 12000, "output": 1500, "cache_creation": 500, "cache_read": 8000}
    sq.set_last_usage("c1", usage)
    assert sq.get_last_usage("c1") == usage


def test_last_usage_set_twice_overwrites() -> None:
    sq = SessionQueue()
    sq.set_last_usage("c1", {"input": 100, "output": 10, "cache_creation": 0, "cache_read": 0})
    sq.set_last_usage("c1", {"input": 200, "output": 20, "cache_creation": 5, "cache_read": 80})
    result = sq.get_last_usage("c1")
    assert result is not None
    assert result["input"] == 200


def test_last_usage_scoped_per_conversation() -> None:
    sq = SessionQueue()
    sq.set_last_usage("c-A", {"input": 1, "output": 2, "cache_creation": 3, "cache_read": 4})
    assert sq.get_last_usage("c-B") is None


def test_last_usage_reset_clears_all() -> None:
    sq = SessionQueue()
    sq.set_last_usage("c1", {"input": 1, "output": 2, "cache_creation": 0, "cache_read": 0})
    sq.reset()
    assert sq.get_last_usage("c1") is None
