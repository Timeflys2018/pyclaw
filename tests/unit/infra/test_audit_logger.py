from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

import pytest

from pyclaw.infra.audit_logger import AuditLogger


@pytest.fixture
def captured(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="pyclaw.audit.tool_approval")
    return caplog


def _last_record(captured: pytest.LogCaptureFixture) -> dict:
    records = [r for r in captured.records if r.name == "pyclaw.audit.tool_approval"]
    assert records, "no audit log lines were emitted"
    return json.loads(records[-1].message)


class TestAuditLoggerSchema:
    def test_emits_required_fields(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_decision(
            conv_id="conv_1",
            session_id="sess_1",
            channel="web",
            tool_name="bash",
            tool_call_id="call_1",
            tier="approval",
            decision="approve",
            decided_by="user_42",
            decided_at=datetime(2026, 5, 16, 10, 30, 45, tzinfo=UTC),
            elapsed_ms=5333,
            user_visible_name="@alice",
        )

        record = _last_record(captured)
        assert record["event"] == "tool_approval_decision"
        assert record["conv_id"] == "conv_1"
        assert record["session_id"] == "sess_1"
        assert record["channel"] == "web"
        assert record["tool_name"] == "bash"
        assert record["tool_call_id"] == "call_1"
        assert record["tier"] == "approval"
        assert record["decision"] == "approve"
        assert record["decided_by"] == "user_42"
        assert record["decided_at"] == "2026-05-16T10:30:45Z"
        assert record["ts"] == "2026-05-16T10:30:45Z"
        assert record["elapsed_ms"] == 5333
        assert record["user_visible_name"] == "@alice"

    def test_optional_fields_omitted_when_none(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_decision(
            conv_id="conv_2",
            session_id="sess_2",
            channel="feishu",
            tool_name="write",
            tool_call_id="call_2",
            tier="read-only",
            decision="deny",
            decided_by="auto:read-only",
        )
        record = _last_record(captured)
        assert "elapsed_ms" not in record
        assert "user_visible_name" not in record

    def test_default_decided_at_is_now_utc(self, captured: pytest.LogCaptureFixture) -> None:
        before = datetime.now(UTC)
        AuditLogger().log_decision(
            conv_id="c",
            session_id="s",
            channel="web",
            tool_name="t",
            tool_call_id="x",
            tier="approval",
            decision="approve",
            decided_by="user_1",
        )
        record = _last_record(captured)
        assert record["decided_at"].endswith("Z")
        parsed = datetime.fromisoformat(record["decided_at"].replace("Z", "+00:00"))
        assert parsed >= before


class TestAuditLoggerDecidedByVariants:
    def test_auto_read_only(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_decision(
            conv_id="c",
            session_id="s",
            channel="web",
            tool_name="bash",
            tool_call_id="x",
            tier="read-only",
            decision="deny",
            decided_by="auto:read-only",
        )
        assert _last_record(captured)["decided_by"] == "auto:read-only"

    def test_auto_yolo(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_decision(
            conv_id="c",
            session_id="s",
            channel="web",
            tool_name="bash",
            tool_call_id="x",
            tier="yolo",
            decision="approve",
            decided_by="auto:yolo",
        )
        assert _last_record(captured)["decided_by"] == "auto:yolo"

    def test_auto_timeout(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_decision(
            conv_id="c",
            session_id="s",
            channel="web",
            tool_name="bash",
            tool_call_id="x",
            tier="approval",
            decision="deny",
            decided_by="auto:timeout",
            elapsed_ms=60000,
        )
        record = _last_record(captured)
        assert record["decided_by"] == "auto:timeout"
        assert record["elapsed_ms"] == 60000

    def test_user_identifier(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_decision(
            conv_id="c",
            session_id="s",
            channel="feishu",
            tool_name="edit",
            tool_call_id="x",
            tier="approval",
            decision="approve",
            decided_by="ou_abc123",
        )
        assert _last_record(captured)["decided_by"] == "ou_abc123"


class TestAuditLoggerOutput:
    def test_emits_at_info_level(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_decision(
            conv_id="c",
            session_id="s",
            channel="web",
            tool_name="bash",
            tool_call_id="x",
            tier="approval",
            decision="approve",
            decided_by="user_1",
        )
        record = next(r for r in captured.records if r.name == "pyclaw.audit.tool_approval")
        assert record.levelno == logging.INFO

    def test_message_is_valid_json(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_decision(
            conv_id="c",
            session_id="s",
            channel="web",
            tool_name="bash",
            tool_call_id="x",
            tier="approval",
            decision="approve",
            decided_by="user_1",
        )
        record = next(r for r in captured.records if r.name == "pyclaw.audit.tool_approval")
        parsed = json.loads(record.message)
        assert parsed["event"] == "tool_approval_decision"

    def test_naive_decided_at_assumed_utc(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_decision(
            conv_id="c",
            session_id="s",
            channel="web",
            tool_name="bash",
            tool_call_id="x",
            tier="approval",
            decision="approve",
            decided_by="user_1",
            decided_at=datetime(2026, 5, 16, 10, 30, 45),
        )
        record = _last_record(captured)
        assert record["decided_at"] == "2026-05-16T10:30:45Z"

    def test_uses_injected_logger(self, captured: pytest.LogCaptureFixture) -> None:
        custom = logging.getLogger("pyclaw.audit.tool_approval.test_inject")
        captured.set_level(logging.INFO, logger=custom.name)
        AuditLogger(logger=custom).log_decision(
            conv_id="c", session_id="s", channel="web",
            tool_name="bash", tool_call_id="x", tier="approval",
            decision="approve", decided_by="user_1",
        )
        records = [r for r in captured.records if r.name == custom.name]
        assert len(records) == 1


class TestTierChangeEvent:
    def test_emits_required_fields(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_tier_change(
            session_id="web:alice:c1",
            channel="web",
            from_tier="approval",
            to_tier="yolo",
            user_id="alice",
            ts=datetime(2026, 5, 16, 10, 30, 45, tzinfo=UTC),
        )
        records = [
            json.loads(r.message)
            for r in captured.records
            if r.name == "pyclaw.audit.tool_approval"
        ]
        assert len(records) == 1
        rec = records[0]
        assert rec["event"] == "permission_tier_changed"
        assert rec["session_id"] == "web:alice:c1"
        assert rec["channel"] == "web"
        assert rec["from_tier"] == "approval"
        assert rec["to_tier"] == "yolo"
        assert rec["user_id"] == "alice"
        assert rec["ts"] == "2026-05-16T10:30:45Z"

    def test_user_id_optional(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_tier_change(
            session_id="s",
            channel="feishu",
            from_tier=None,
            to_tier="approval",
        )
        records = [
            json.loads(r.message)
            for r in captured.records
            if r.name == "pyclaw.audit.tool_approval"
        ]
        assert len(records) == 1
        rec = records[0]
        assert rec["from_tier"] is None
        assert "user_id" not in rec

    def test_emits_at_info_level(self, captured: pytest.LogCaptureFixture) -> None:
        AuditLogger().log_tier_change(
            session_id="s", channel="web",
            from_tier="approval", to_tier="yolo",
        )
        record = next(
            r for r in captured.records if r.name == "pyclaw.audit.tool_approval"
        )
        assert record.levelno == logging.INFO
