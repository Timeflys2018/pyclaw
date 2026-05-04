from __future__ import annotations

from typing import Any

from pyclaw.core.agent.tools.registry import ToolContext, error_result, text_result
from pyclaw.models import ToolResult


class SkillViewTool:
    name = "skill_view"
    description = (
        "Load full instructions for a named skill."
        " Use when a task matches a skill from the available skills list."
    )
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Skill name to view"},
        },
        "required": ["name"],
    }
    side_effect = False

    def __init__(self, skill_provider: Any) -> None:
        self._provider = skill_provider

    async def execute(self, args: dict[str, Any], context: ToolContext) -> ToolResult:
        call_id = args.get("_call_id", "")
        name = args.get("name", "")
        detail = self._provider.get_skill_detail(name)
        if not detail:
            return error_result(call_id, f"Skill '{name}' not found.")
        return text_result(call_id, detail)
