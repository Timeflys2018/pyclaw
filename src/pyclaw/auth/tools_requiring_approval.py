from __future__ import annotations

from pyclaw.auth.profile import UserProfile


def resolve_tools_requiring_approval(
    *,
    profile: UserProfile | None,
    channel_default: list[str],
) -> list[str]:
    """Resolve effective ``tools_requiring_approval`` per Sprint 3 4-slot review F2.

    REPLACE semantics:
    - ``profile is None`` or ``profile.tools_requiring_approval is None`` → channel default
    - non-None list (including ``[]``) → REPLACES channel default verbatim
    """
    if profile is None or profile.tools_requiring_approval is None:
        return list(channel_default)
    return list(profile.tools_requiring_approval)
