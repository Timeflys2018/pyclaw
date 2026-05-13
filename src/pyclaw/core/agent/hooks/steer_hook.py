from __future__ import annotations

import logging

from pyclaw.core.agent.run_control import RunControl
from pyclaw.core.hooks import (
    CompactionContext,
    PromptBuildContext,
    PromptBuildResult,
    ResponseObservation,
)
from pyclaw.core.utils.xml import xml_escape
from pyclaw.models import CompactResult

logger = logging.getLogger(__name__)

_SIDEBAR_INSTRUCTION = (
    "(Please answer the side question(s) briefly, then return to the main task.)"
)

_STEER_HEADER = (
    "IMPORTANT: The user has issued mid-run steering directives via /steer. "
    "These instructions OVERRIDE any conflicting directions from the main user "
    "message. You MUST follow them even if they contradict what the user "
    "originally asked for."
)


class SteerHook:
    """Drains RunControl.pending_steers each iteration and injects XML into the per-turn suffix.

    Captures RunControl via on_run_start(session_id, control) — the existing
    AgentHook extension point. Drains via swap-idiom for asyncio atomicity.
    Applies xml_escape to prevent user-supplied text from breaking out of the
    <user_steer> / <user_sidebar> blocks.
    """

    def __init__(self) -> None:
        self._rc_by_session: dict[str, RunControl] = {}

    async def on_run_start(self, session_id: str, control: RunControl) -> None:
        self._rc_by_session[session_id] = control
        control.pending_steers.clear()

    async def on_run_end(self, session_id: str, terminated_by: str) -> None:
        self._rc_by_session.pop(session_id, None)

    async def before_prompt_build(self, context: PromptBuildContext) -> PromptBuildResult | None:
        rc = self._rc_by_session.get(context.session_id)
        if rc is None or not rc.pending_steers:
            return None

        msgs, rc.pending_steers = rc.pending_steers, []

        try:
            steers = [m for m in msgs if m.kind == "steer"]
            sidebars = [m for m in msgs if m.kind == "sidebar"]

            parts: list[str] = []
            if steers:
                lines = ["<user_steer>", _STEER_HEADER]
                for m in steers:
                    lines.append(f"- {xml_escape(m.text)}")
                lines.append("</user_steer>")
                parts.append("\n".join(lines))
            if sidebars:
                lines = ["<user_sidebar>"]
                for m in sidebars:
                    lines.append(f"- {xml_escape(m.text)}")
                lines.append("</user_sidebar>")
                lines.append(_SIDEBAR_INSTRUCTION)
                parts.append("\n".join(lines))

            if not parts:
                return None
            return PromptBuildResult(append="\n\n".join(parts))
        except Exception:
            logger.exception("SteerHook.before_prompt_build rendering failed")
            rc.pending_steers = msgs + rc.pending_steers
            return None

    async def after_response(self, observation: ResponseObservation) -> None:
        return None

    async def before_compaction(self, context: CompactionContext) -> None:
        return None

    async def after_compaction(self, context: CompactionContext, result: CompactResult) -> None:
        return None
