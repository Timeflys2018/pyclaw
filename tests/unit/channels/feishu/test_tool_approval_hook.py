from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.feishu.approval_registry import FeishuApprovalRegistry
from pyclaw.channels.feishu.tool_approval_hook import FeishuToolApprovalHook
from pyclaw.infra.audit_logger import AuditLogger
from pyclaw.infra.settings import FeishuSettings
from pyclaw.infra.task_manager import TaskManager


@pytest.fixture
def settings() -> FeishuSettings:
    return FeishuSettings(
        toolsRequiringApproval=["bash", "write", "edit"],
        toolApprovalTimeoutSeconds=10,
    )


@pytest.fixture
def fast_settings() -> FeishuSettings:
    return FeishuSettings(
        toolsRequiringApproval=["bash", "write", "edit"],
        toolApprovalTimeoutSeconds=1,
    )


@pytest.fixture
def registry() -> FeishuApprovalRegistry:
    return FeishuApprovalRegistry()


@pytest.fixture
def audit_logger() -> AuditLogger:
    return AuditLogger()


@pytest.fixture
def captured(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.INFO, logger="pyclaw.audit.tool_approval")
    return caplog


@pytest.fixture
def task_manager() -> TaskManager:
    return TaskManager()


def _audit_records(captured: pytest.LogCaptureFixture) -> list[dict]:
    return [
        json.loads(r.message) for r in captured.records if r.name == "pyclaw.audit.tool_approval"
    ]


def _mock_client() -> Any:
    client = MagicMock()
    client.send_interactive_card = AsyncMock(return_value="msg_1")
    client.patch_interactive_card = AsyncMock(return_value=True)
    return client


class TestShouldGate:
    """Sprint 2.0.1 hotfix: should_gate predicate replaces in-hook fast-path."""

    def test_listed_tool_returns_true(
        self,
        registry: FeishuApprovalRegistry,
        settings: FeishuSettings,
        audit_logger: AuditLogger,
    ) -> None:
        hook = FeishuToolApprovalHook(
            client=_mock_client(),
            registry=registry,
            settings=settings,
            audit_logger=audit_logger,
        )
        assert hook.should_gate("bash") is True
        assert hook.should_gate("write") is True

    def test_unlisted_tool_returns_false(
        self,
        registry: FeishuApprovalRegistry,
        settings: FeishuSettings,
        audit_logger: AuditLogger,
    ) -> None:
        hook = FeishuToolApprovalHook(
            client=_mock_client(),
            registry=registry,
            settings=settings,
            audit_logger=audit_logger,
        )
        assert hook.should_gate("read") is False
        assert hook.should_gate("fs:list_directory") is False


class TestShouldGateUserProfileReplace:
    """Sprint 3 4-slot review F2 — Feishu per-user tools_requiring_approval REPLACE."""

    def test_user_override_replaces_channel_default(
        self,
        registry: FeishuApprovalRegistry,
        settings: FeishuSettings,
        audit_logger: AuditLogger,
    ) -> None:
        from types import SimpleNamespace

        from pyclaw.auth.profile import UserProfile

        hook = FeishuToolApprovalHook(
            client=_mock_client(),
            registry=registry,
            settings=settings,
            audit_logger=audit_logger,
        )
        profile = UserProfile(
            channel="feishu", user_id="ou_alice", tools_requiring_approval=["bash"],
        )
        ctx = SimpleNamespace(user_profile=profile)

        assert hook.should_gate("bash", ctx) is True
        assert hook.should_gate("write", ctx) is False
        assert hook.should_gate("edit", ctx) is False

    def test_user_empty_list_gates_nothing(
        self,
        registry: FeishuApprovalRegistry,
        settings: FeishuSettings,
        audit_logger: AuditLogger,
    ) -> None:
        from types import SimpleNamespace

        from pyclaw.auth.profile import UserProfile

        hook = FeishuToolApprovalHook(
            client=_mock_client(),
            registry=registry,
            settings=settings,
            audit_logger=audit_logger,
        )
        profile = UserProfile(
            channel="feishu", user_id="ou_alice", tools_requiring_approval=[],
        )
        ctx = SimpleNamespace(user_profile=profile)

        assert hook.should_gate("bash", ctx) is False

    def test_ctx_none_falls_back_to_channel_default(
        self,
        registry: FeishuApprovalRegistry,
        settings: FeishuSettings,
        audit_logger: AuditLogger,
    ) -> None:
        hook = FeishuToolApprovalHook(
            client=_mock_client(),
            registry=registry,
            settings=settings,
            audit_logger=audit_logger,
        )
        assert hook.should_gate("bash", None) is True


class TestUserApproval:
    @pytest.mark.asyncio
    async def test_originator_approves_returns_approve(
        self,
        registry: FeishuApprovalRegistry,
        settings: FeishuSettings,
        audit_logger: AuditLogger,
        task_manager: TaskManager,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        client = _mock_client()
        hook = FeishuToolApprovalHook(
            client=client,
            registry=registry,
            settings=settings,
            audit_logger=audit_logger,
            task_manager=task_manager,
        )

        async def respond_later() -> None:
            await asyncio.sleep(0.05)
            registry.set_decision(
                conv_id="feishu:cli_x:ou_a",
                tool_call_id="x1",
                approved=True,
                operator_open_id="ou_a",
            )

        gate = asyncio.create_task(respond_later())
        decisions = await hook.before_tool_execution(
            [{"id": "x1", "name": "bash", "args": {"command": "ls"}}],
            session_id="feishu:cli_x:ou_a",
            tier="approval",
        )
        await gate

        assert decisions == ["approve"]
        client.send_interactive_card.assert_awaited_once()
        client.patch_interactive_card.assert_awaited()
        rec = _audit_records(captured)[-1]
        assert rec["channel"] == "feishu"
        assert rec["decision"] == "approve"
        assert rec["decided_by"] == "ou_a"

    @pytest.mark.asyncio
    async def test_originator_denies_returns_deny(
        self,
        registry: FeishuApprovalRegistry,
        settings: FeishuSettings,
        audit_logger: AuditLogger,
        task_manager: TaskManager,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        client = _mock_client()
        hook = FeishuToolApprovalHook(
            client=client,
            registry=registry,
            settings=settings,
            audit_logger=audit_logger,
            task_manager=task_manager,
        )

        async def respond_later() -> None:
            await asyncio.sleep(0.05)
            registry.set_decision(
                conv_id="feishu:cli_x:ou_a",
                tool_call_id="x1",
                approved=False,
                operator_open_id="ou_a",
            )

        gate = asyncio.create_task(respond_later())
        decisions = await hook.before_tool_execution(
            [{"id": "x1", "name": "bash", "args": {"command": "ls"}}],
            session_id="feishu:cli_x:ou_a",
            tier="approval",
        )
        await gate

        assert decisions == ["deny"]
        rec = _audit_records(captured)[-1]
        assert rec["decision"] == "deny"


class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_returns_deny_with_audit(
        self,
        registry: FeishuApprovalRegistry,
        fast_settings: FeishuSettings,
        audit_logger: AuditLogger,
        task_manager: TaskManager,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        client = _mock_client()
        hook = FeishuToolApprovalHook(
            client=client,
            registry=registry,
            settings=fast_settings,
            audit_logger=audit_logger,
            task_manager=task_manager,
        )

        decisions = await hook.before_tool_execution(
            [{"id": "x1", "name": "bash", "args": {}}],
            session_id="feishu:cli_x:ou_a",
            tier="approval",
        )
        assert decisions == ["deny"]
        rec = _audit_records(captured)[-1]
        assert rec["decision"] == "deny"
        assert rec["decided_by"] == "auto:timeout"
        assert rec["elapsed_ms"] >= 1000

    @pytest.mark.asyncio
    async def test_timeout_cleans_up_pending(
        self,
        registry: FeishuApprovalRegistry,
        fast_settings: FeishuSettings,
        audit_logger: AuditLogger,
        task_manager: TaskManager,
    ) -> None:
        client = _mock_client()
        hook = FeishuToolApprovalHook(
            client=client,
            registry=registry,
            settings=fast_settings,
            audit_logger=audit_logger,
            task_manager=task_manager,
        )
        await hook.before_tool_execution(
            [{"id": "x1", "name": "bash", "args": {}}],
            session_id="feishu:cli_x:ou_a",
            tier="approval",
        )
        assert registry.get(conv_id="feishu:cli_x:ou_a", tool_call_id="x1") is None


class TestPostFailFallback:
    @pytest.mark.asyncio
    async def test_post_failure_auto_denies(
        self,
        registry: FeishuApprovalRegistry,
        settings: FeishuSettings,
        audit_logger: AuditLogger,
        captured: pytest.LogCaptureFixture,
    ) -> None:
        client = MagicMock()
        client.send_interactive_card = AsyncMock(side_effect=RuntimeError("boom"))
        client.patch_interactive_card = AsyncMock(return_value=True)
        hook = FeishuToolApprovalHook(
            client=client,
            registry=registry,
            settings=settings,
            audit_logger=audit_logger,
        )
        decisions = await hook.before_tool_execution(
            [{"id": "x1", "name": "bash", "args": {}}],
            session_id="feishu:cli_x:ou_a",
            tier="approval",
        )
        assert decisions == ["deny"]
        rec = _audit_records(captured)[-1]
        assert rec["decided_by"] == "auto:post-failed"


class TestReceiveIdResolution:
    def test_resolve_p2p(self) -> None:
        rid, rid_type = FeishuToolApprovalHook._resolve_receive_id(
            "feishu:cli_x:ou_a",
            "ou_a",
        )
        assert (rid, rid_type) == ("ou_a", "open_id")

    def test_resolve_group_chat(self) -> None:
        rid, rid_type = FeishuToolApprovalHook._resolve_receive_id(
            "feishu:cli_x:chat:oc_xyz",
            "ou_a",
        )
        assert (rid, rid_type) == ("oc_xyz", "chat_id")

    def test_resolve_falls_back_to_originator(self) -> None:
        rid, rid_type = FeishuToolApprovalHook._resolve_receive_id(
            "weird-format",
            "ou_b",
        )
        assert (rid, rid_type) == ("ou_b", "open_id")
