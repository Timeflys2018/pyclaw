from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class AgentHook(Protocol):
    async def before_prompt_build(self, context: dict) -> dict:
        ...

    async def after_response(self, context: dict, response: str) -> None:
        ...
