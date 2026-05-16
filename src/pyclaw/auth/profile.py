from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pyclaw.core.hooks import PermissionTier

Channel = Literal["web", "feishu"]
Role = Literal["admin", "member"]


@dataclass(frozen=True)
class UserProfile:
    channel: Channel
    user_id: str
    role: Role = "member"
    tier_default: PermissionTier | None = None
    tools_requiring_approval: list[str] | None = None
    env_allowlist: list[str] | None = None
    sandbox_overrides: dict[str, Any] | None = None
