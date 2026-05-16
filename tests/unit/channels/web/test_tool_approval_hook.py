from __future__ import annotations

import asyncio
import json
import logging

import pytest

from pyclaw.channels.web.chat import SessionQueue
from pyclaw.channels.web.tool_approval_hook import WebToolApprovalHook
from pyclaw.infra.audit_logger import AuditLogger
from pyclaw.infra.settings import WebSettings


@pytest.fixture
def queue() -> SessionQueue:
    return SessionQueue()


@pytest.fixture
def settings() -> WebSettings:
    return WebSettings(
        toolsRequiringApproval=["bash", "write", "edit"],
        toolApprovalTimeoutSeconds=10,
    )


@pytest.fixture
def fast_settings() -> WebSettings:
    return WebSettings(
        toolsRequiringApproval=["bash", "write", "edit"],
        toolApprovalTimeoutSeconds=1,
    )


@pytest.fixture
def audit_logger() -> AuditLogger:
    return AuditLogger()


@pytest.fixture
def captured(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="pyclaw.audit.tool_approval")
    return caplog


def _audit_records(captured: pytest.LogCaptureFixture) -> list[dict]:
    return [
        json.loads(r.message) for r in captured.records if r.name == "pyclaw.audit.tool_approval"
    ]


class TestShouldGate:
    """Sprint 2.0.1 hotfix: should_gate is the runner's per-call gating predicate.

    Hook no longer fast-paths non-gated calls itself. The runner uses
    should_gate(name) before deciding to emit ToolApprovalRequest.
    """

    def test_listed_tool_returns_true(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )
        assert hook.should_gate("bash") is True
        assert hook.should_gate("write") is True
        assert hook.should_gate("edit") is True

    def test_unlisted_tool_returns_false(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )
        assert hook.should_gate("read") is False
        assert hook.should_gate("fs:search_files") is False
        assert hook.should_gate("memorize") is False

    def test_should_gate_is_synchronous(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        import inspect

        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )
        assert not inspect.iscoroutinefunction(hook.should_gate)


class TestShouldGateUserProfileReplace:
    """Sprint 3 4-slot review F2 — per-user tools_requiring_approval REPLACE."""

    def test_user_override_replaces_channel_default(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        from types import SimpleNamespace

        from pyclaw.auth.profile import UserProfile

        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )
        profile = UserProfile(
            channel="web", user_id="alice", tools_requiring_approval=["bash"],
        )
        ctx = SimpleNamespace(user_profile=profile)

        assert hook.should_gate("bash", ctx) is True
        assert hook.should_gate("write", ctx) is False
        assert hook.should_gate("edit", ctx) is False

    def test_user_none_falls_through_to_channel_default(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        from types import SimpleNamespace

        from pyclaw.auth.profile import UserProfile

        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )
        profile = UserProfile(
            channel="web", user_id="alice", tools_requiring_approval=None,
        )
        ctx = SimpleNamespace(user_profile=profile)

        assert hook.should_gate("bash", ctx) is True
        assert hook.should_gate("write", ctx) is True

    def test_user_empty_list_gates_nothing(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        from types import SimpleNamespace

        from pyclaw.auth.profile import UserProfile

        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )
        profile = UserProfile(
            channel="web", user_id="alice", tools_requiring_approval=[],
        )
        ctx = SimpleNamespace(user_profile=profile)

        assert hook.should_gate("bash", ctx) is False
        assert hook.should_gate("write", ctx) is False

    def test_ctx_none_falls_back_to_channel_default(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )
        assert hook.should_gate("bash", None) is True
        assert hook.should_gate("read", None) is False

    def test_ctx_without_user_profile_attr_falls_back(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        from types import SimpleNamespace

        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )
        ctx = SimpleNamespace()
        assert hook.should_gate("bash", ctx) is True


class TestUserApproval:
    @pytest.mark.asyncio
    async def test_user_approves_returns_approve(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )

        async def respond_later() -> None:
            await asyncio.sleep(0.01)
            queue.set_approval_decision("s1", "c1", True)

        gate = asyncio.create_task(respond_later())
        decisions = await hook.before_tool_execution(
            [{"id": "c1", "name": "bash", "args": {}}],
            session_id="s1",
            tier="approval",
        )
        await gate

        assert decisions == ["approve"]
        rec = _audit_records(captured)[-1]
        assert rec["decision"] == "approve"
        assert rec["decided_by"] == "user"
        assert "elapsed_ms" in rec

    @pytest.mark.asyncio
    async def test_user_denies_returns_deny(
        self,
        queue: SessionQueue,
        settings: WebSettings,
        audit_logger: AuditLogger,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=settings,
            audit_logger=audit_logger,
        )

        async def respond_later() -> None:
            await asyncio.sleep(0.01)
            queue.set_approval_decision("s1", "c1", False)

        gate = asyncio.create_task(respond_later())
        decisions = await hook.before_tool_execution(
            [{"id": "c1", "name": "bash", "args": {}}],
            session_id="s1",
            tier="approval",
        )
        await gate

        assert decisions == ["deny"]
        rec = _audit_records(captured)[-1]
        assert rec["decision"] == "deny"
        assert rec["decided_by"] == "user"


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_deny_and_logs_auto_timeout(
        self,
        queue: SessionQueue,
        fast_settings: WebSettings,
        audit_logger: AuditLogger,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=fast_settings,
            audit_logger=audit_logger,
        )

        decisions = await hook.before_tool_execution(
            [{"id": "c1", "name": "bash", "args": {}}],
            session_id="s1",
            tier="approval",
        )

        assert decisions == ["deny"]
        rec = _audit_records(captured)[-1]
        assert rec["decision"] == "deny"
        assert rec["decided_by"] == "auto:timeout"
        assert rec["elapsed_ms"] >= 1000

    @pytest.mark.asyncio
    async def test_timeout_cleans_up_pending_entry(
        self,
        queue: SessionQueue,
        fast_settings: WebSettings,
        audit_logger: AuditLogger,
    ) -> None:
        hook = WebToolApprovalHook(
            session_queue=queue,
            settings=fast_settings,
            audit_logger=audit_logger,
        )
        await hook.before_tool_execution(
            [{"id": "c1", "name": "bash", "args": {}}],
            session_id="s1",
            tier="approval",
        )
        assert queue._approval_pending == {}


class TestSessionQueuePendingApi:
    @pytest.mark.asyncio
    async def test_set_decision_signals_event(self, queue: SessionQueue) -> None:
        pending = queue.create_pending("s1", "c1")
        assert not pending.event.is_set()
        queue.set_approval_decision("s1", "c1", True)
        assert pending.event.is_set()
        assert pending.approved is True

    @pytest.mark.asyncio
    async def test_set_decision_without_pending_is_safe(self, queue: SessionQueue) -> None:
        queue.set_approval_decision("s1", "c1", True)
        assert queue.get_approval_decision("s1", "c1") is True

    @pytest.mark.asyncio
    async def test_reset_signals_outstanding_pending_with_deny(
        self,
        queue: SessionQueue,
    ) -> None:
        pending = queue.create_pending("s1", "c1")
        queue.reset()
        assert pending.event.is_set()
        assert pending.approved is False

    @pytest.mark.asyncio
    async def test_discard_pending_removes_entry(self, queue: SessionQueue) -> None:
        queue.create_pending("s1", "c1")
        queue.discard_pending("s1", "c1")
        assert queue._approval_pending == {}


class TestMaybeRecordTierChange:
    def test_first_call_returns_none(self, queue: SessionQueue) -> None:
        assert queue.maybe_record_tier_change("c1", "approval") is None

    def test_same_tier_returns_none(self, queue: SessionQueue) -> None:
        queue.maybe_record_tier_change("c1", "approval")
        assert queue.maybe_record_tier_change("c1", "approval") is None

    def test_different_tier_returns_previous(self, queue: SessionQueue) -> None:
        queue.maybe_record_tier_change("c1", "approval")
        previous = queue.maybe_record_tier_change("c1", "yolo")
        assert previous == "approval"

    def test_tracks_per_conversation(self, queue: SessionQueue) -> None:
        queue.maybe_record_tier_change("c1", "approval")
        queue.maybe_record_tier_change("c2", "yolo")
        assert queue.maybe_record_tier_change("c1", "yolo") == "approval"
        assert queue.maybe_record_tier_change("c2", "yolo") is None

    def test_reset_clears_tracking(self, queue: SessionQueue) -> None:
        queue.maybe_record_tier_change("c1", "approval")
        queue.reset()
        assert queue.maybe_record_tier_change("c1", "yolo") is None
