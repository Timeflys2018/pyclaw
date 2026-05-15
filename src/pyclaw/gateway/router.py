from __future__ import annotations

import logging
from typing import Any, Literal

from pyclaw.gateway.affinity import AffinityRegistry
from pyclaw.gateway.forwarder import ForwardPublisher

logger = logging.getLogger(__name__)

RouteResult = Literal["local", "forwarded"]


class GatewayRouter:
    def __init__(self, affinity: AffinityRegistry, forwarder: ForwardPublisher) -> None:
        self._affinity = affinity
        self._forwarder = forwarder

    @property
    def affinity(self) -> AffinityRegistry:
        return self._affinity

    @property
    def worker_id(self) -> str:
        return self._affinity.worker_id

    async def route(self, session_key: str, event_payload: dict[str, Any]) -> RouteResult:
        try:
            return await self._route_inner(session_key, event_payload)
        except (ConnectionError, TimeoutError, OSError):
            logger.warning(
                "gateway router: redis error during routing for session_key=%s; processing locally",
                session_key,
                exc_info=True,
            )
            return "local"

    async def _route_inner(self, session_key: str, event_payload: dict[str, Any]) -> RouteResult:
        owner = await self._affinity.resolve(session_key)

        if self._affinity.is_mine(owner):
            await self._affinity.renew(session_key)
            return "local"

        if owner is None:
            claimed = await self._affinity.claim(session_key)
            if claimed:
                return "local"
            owner = await self._affinity.resolve(session_key)
            if owner is None or self._affinity.is_mine(owner):
                return "local"

        delivered = await self._forwarder.forward(owner, event_payload)
        if not delivered:
            logger.info(
                "gateway: target worker %s not reachable; force-claiming session_key=%s",
                owner,
                session_key,
            )
            await self._affinity.force_claim(session_key)
            return "local"

        return "forwarded"
