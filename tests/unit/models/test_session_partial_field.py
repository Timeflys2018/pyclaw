"""Tests for MessageEntry.partial field.

Covers spec runner-partial-persistence Requirement "MessageEntry exposes a partial flag":
- partial defaults to False when omitted
- partial=True is preserved through serialization round-trip
- backward compatibility (old JSON without partial → partial=False)
- forward compatibility (new JSON with unknown future fields → extra="ignore")
"""

from __future__ import annotations

from pyclaw.models import MessageEntry


def test_partial_default_false() -> None:
    """Scenario: partial defaults to False when omitted."""
    entry = MessageEntry(id="x", parent_id=None, role="assistant", content="hi")
    assert entry.partial is False


def test_partial_round_trip_serialization() -> None:
    """Scenario: partial=True is preserved through serialization round-trip."""
    entry = MessageEntry(id="x", parent_id=None, role="assistant", content="hi", partial=True)
    data = entry.model_dump()
    assert data["partial"] is True

    restored = MessageEntry.model_validate(data)
    assert restored.partial is True


def test_backward_compat_old_json_without_partial() -> None:
    """Scenario: backward compatibility — old SessionTree JSON without partial loads with partial=False."""
    old_data = {
        "id": "x",
        "parent_id": None,
        "timestamp": "2026-01-01T00:00:00",
        "type": "message",
        "role": "assistant",
        "content": "hi",
        "tool_calls": None,
        "tool_call_id": None,
    }
    entry = MessageEntry.model_validate(old_data)
    assert entry.partial is False


def test_forward_compat_extra_ignored() -> None:
    """Scenario: forward compatibility — new JSON with unknown future fields deserializes via extra="ignore".

    This empirically confirms design R1: rolling back to older code that lacks
    the partial field still successfully loads SessionTrees written by newer code.
    """
    future_data = {
        "id": "x",
        "parent_id": None,
        "timestamp": "2026-01-01T00:00:00",
        "type": "message",
        "role": "assistant",
        "content": "hi",
        "tool_calls": None,
        "tool_call_id": None,
        "partial": True,
        "future_field_added_in_v4": "xyz",
        "another_unknown": {"nested": "value"},
    }
    entry = MessageEntry.model_validate(future_data)
    assert entry.partial is True
    assert entry.role == "assistant"
    assert entry.content == "hi"
