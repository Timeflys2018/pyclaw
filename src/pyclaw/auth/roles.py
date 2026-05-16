from __future__ import annotations

from typing import Literal

Role = Literal["admin", "member"]

BUILTIN_ROLES: frozenset[str] = frozenset(("admin", "member"))
