from __future__ import annotations

from pyclaw.models.session import SessionHeader, SessionHistorySummary


def test_session_header_new_fields_have_defaults() -> None:
    header = SessionHeader(id="sess-1", workspace_id="ws-1", agent_id="default")
    assert header.session_key == ""
    assert header.last_interaction_at is None
    assert header.idle_minutes_override is None


def test_session_header_session_key_set() -> None:
    header = SessionHeader(
        id="sess-1",
        workspace_id="ws-1",
        agent_id="default",
        session_key="feishu:cli_xxx:ou_abc",
    )
    assert header.session_key == "feishu:cli_xxx:ou_abc"


def test_session_header_last_interaction_at_none_by_default() -> None:
    header = SessionHeader(id="sess-1", workspace_id="ws-1", agent_id="default")
    assert header.last_interaction_at is None


def test_session_header_last_interaction_at_set() -> None:
    ts = "2026-05-01T12:00:00+00:00"
    header = SessionHeader(
        id="sess-1",
        workspace_id="ws-1",
        agent_id="default",
        last_interaction_at=ts,
    )
    assert header.last_interaction_at == ts


def test_session_header_parent_session_still_works() -> None:
    header = SessionHeader(
        id="sess-new",
        workspace_id="ws-1",
        agent_id="default",
        parent_session="sess-old",
    )
    assert header.parent_session == "sess-old"


def test_session_history_summary_fields() -> None:
    summary = SessionHistorySummary(
        session_id="feishu:cli_xxx:ou_abc:s:a1b2c3d4",
        created_at="2026-05-01T00:00:00+00:00",
        message_count=5,
        last_message_at="2026-05-01T12:00:00+00:00",
        parent_session_id=None,
    )
    assert summary.session_id == "feishu:cli_xxx:ou_abc:s:a1b2c3d4"
    assert summary.message_count == 5
    assert summary.parent_session_id is None


def test_session_history_summary_optional_fields() -> None:
    summary = SessionHistorySummary(
        session_id="s1",
        created_at="2026-05-01T00:00:00+00:00",
        message_count=0,
        last_message_at=None,
        parent_session_id=None,
    )
    assert summary.last_message_at is None
