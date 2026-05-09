"""Tests for session_export rendering of partial=True MessageEntry.

Covers spec runner-partial-persistence Requirement
"session_export marks partial assistant entries with (interrupted) suffix".
"""

from __future__ import annotations

from pyclaw.core.session_export import render_session_markdown
from pyclaw.models import MessageEntry, SessionHeader, SessionTree


def _build_tree_with(entry: MessageEntry) -> SessionTree:
    header = SessionHeader(id="ses-test", workspace_id="default", agent_id="main")
    tree = SessionTree(header=header)
    tree.append(entry)
    return tree


def test_partial_assistant_renders_with_interrupted_suffix() -> None:
    """Scenario: partial=True assistant entry renders with (interrupted) suffix."""
    entry = MessageEntry(
        id="msg_abc",
        parent_id=None,
        timestamp="2026-05-10T12:34:56",
        role="assistant",
        content="hello",
        partial=True,
    )
    output = render_session_markdown(_build_tree_with(entry))

    assert "## ASSISTANT (interrupted) — `msg_abc` (2026-05-10T12:34:56)" in output
    assert "hello" in output
    assert "[note:" not in output


def test_non_partial_assistant_renders_without_suffix() -> None:
    """Scenario: partial=False assistant entry renders without (interrupted) suffix."""
    entry = MessageEntry(
        id="msg_def",
        parent_id=None,
        timestamp="2026-05-10T12:35:00",
        role="assistant",
        content="bye",
        partial=False,
    )
    output = render_session_markdown(_build_tree_with(entry))

    assert "## ASSISTANT — `msg_def`" in output
    assert "(interrupted)" not in output
    assert "bye" in output
