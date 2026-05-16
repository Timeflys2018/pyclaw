from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Literal

from pyclaw.core.hooks import ApprovalDecision, PermissionTier

_AUDIT_LOGGER_NAME = "pyclaw.audit.tool_approval"

DecidedBy = Literal[
    "auto:read-only",
    "auto:yolo",
    "auto:timeout",
    "user",
]


class AuditLogger:
    """Emit structured JSON audit lines for every tool-approval decision.

    Per spec ``tool-approval-tiers`` requirement
    "Audit log emits structured JSON line per decision" and design D11.

    A single shared instance is constructed in ``app.py`` lifespan and passed
    to both ``WebToolApprovalHook`` and ``FeishuToolApprovalHook``. Each
    decision (auto or user-driven) calls :meth:`log_decision`, which writes
    one INFO-level JSON line to the ``pyclaw.audit.tool_approval`` logger.

    Sprint 1 stores audit data only in the logger sink (stdout / file /
    journald per deployment). Persistence to Redis/SQLite is deferred to
    Sprint 1.1 (TA2 follow-up).
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger(_AUDIT_LOGGER_NAME)

    def log_decision(
        self,
        *,
        conv_id: str,
        session_id: str,
        channel: Literal["web", "feishu"],
        tool_name: str,
        tool_call_id: str,
        tier: PermissionTier,
        decision: ApprovalDecision,
        decided_by: str,
        decided_at: datetime | None = None,
        elapsed_ms: int | None = None,
        user_visible_name: str | None = None,
        tier_source: str | None = None,
        forced_server: str | None = None,
        user_id: str | None = None,
        role: Literal["admin", "member"] | None = None,
        sandbox_backend: Literal["srt", "none"] | None = None,
        reason: str | None = None,
    ) -> None:
        """Emit one audit line. ``decided_by`` is one of :data:`DecidedBy` or
        a user identifier string (Web ``user_id`` or Feishu ``open_id``).

        Sprint 2 added ``tier_source``/``forced_server`` for the de-escalation
        path; Sprint 3 adds ``user_id``/``role``/``sandbox_backend`` for the
        per-user permission model + sandbox enforcement. All new fields are
        optional and omitted when None to preserve Sprint 1 grep-based
        consumer compatibility.
        """
        decided_at = decided_at or datetime.now(UTC)
        payload: dict[str, object] = {
            "event": "tool_approval_decision",
            "ts": _iso(decided_at),
            "conv_id": conv_id,
            "session_id": session_id,
            "channel": channel,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "tier": tier,
            "decision": decision,
            "decided_by": decided_by,
            "decided_at": _iso(decided_at),
        }
        if elapsed_ms is not None:
            payload["elapsed_ms"] = elapsed_ms
        if user_visible_name is not None:
            payload["user_visible_name"] = user_visible_name
        if tier_source is not None:
            payload["tier_source"] = tier_source
        if forced_server is not None:
            payload["forced_server"] = forced_server
        if user_id is not None:
            payload["user_id"] = user_id
        if role is not None:
            payload["role"] = role
        if sandbox_backend is not None:
            payload["sandbox_backend"] = sandbox_backend
        if reason is not None:
            payload["reason"] = reason

        self._logger.info(json.dumps(payload, separators=(",", ":"), sort_keys=False))

    def log_tier_change(
        self,
        *,
        session_id: str,
        channel: Literal["web", "feishu"],
        from_tier: PermissionTier | None,
        to_tier: PermissionTier,
        user_id: str | None = None,
        ts: datetime | None = None,
    ) -> None:
        """Emit a single line marking that a session switched permission tier.

        Lets operators answer "when did this session enter yolo?" with a single
        grep instead of inspecting every tool_approval_decision line. Per the
        Kubernetes-audit precedent: structural bookmarks for tier changes
        complement (don't replace) per-action audit lines.
        """
        ts = ts or datetime.now(UTC)
        payload: dict[str, object] = {
            "event": "permission_tier_changed",
            "ts": _iso(ts),
            "session_id": session_id,
            "channel": channel,
            "from_tier": from_tier,
            "to_tier": to_tier,
        }
        if user_id is not None:
            payload["user_id"] = user_id
        self._logger.info(json.dumps(payload, separators=(",", ":"), sort_keys=False))


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")
