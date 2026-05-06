"""Tests for MemoryNudgeHook."""

from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook


def test_reset_counter_removes_entry():
    nudge = MemoryNudgeHook(interval=10)
    nudge._counts["ses_1"] = 5
    nudge.reset_counter("ses_1")
    assert "ses_1" not in nudge._counts


def test_reset_counter_idempotent_for_unknown_session():
    nudge = MemoryNudgeHook(interval=10)
    nudge.reset_counter("never_seen")
    assert "never_seen" not in nudge._counts
