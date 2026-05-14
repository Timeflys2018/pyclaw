from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)

ForwardHandler = Callable[[dict[str, Any]], Awaitable[None]]


class ForwardPublisher:
    def __init__(self, redis_client: Any, *, prefix: str = "pyclaw:forward:") -> None:
        self._redis = redis_client
        self._prefix = prefix

    def channel_for(self, worker_id: str) -> str:
        return f"{self._prefix}{worker_id}"

    async def forward(self, target_worker_id: str, event_payload: dict[str, Any]) -> bool:
        channel = self.channel_for(target_worker_id)
        message = json.dumps(event_payload, default=str)
        subscribers = await self._redis.publish(channel, message)
        return int(subscribers) > 0


class ForwardConsumer:
    def __init__(
        self,
        redis_client: Any,
        worker_id: str,
        handler_fn: ForwardHandler,
        *,
        prefix: str = "pyclaw:forward:",
    ) -> None:
        self._redis = redis_client
        self._worker_id = worker_id
        self._handler = handler_fn
        self._prefix = prefix
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._pubsub: Any = None

    @property
    def channel(self) -> str:
        return f"{self._prefix}{self._worker_id}"

    async def _run_loop(self) -> None:
        backoff_s = 1.0
        max_backoff_s = 30.0
        while not self._stopping:
            try:
                self._pubsub = self._redis.pubsub()
                await self._pubsub.subscribe(self.channel)
                logger.info("forward consumer subscribed to %s", self.channel)
                backoff_s = 1.0
                async for message in self._pubsub.listen():
                    if self._stopping:
                        break
                    if message.get("type") != "message":
                        continue
                    raw = message.get("data")
                    if isinstance(raw, bytes):
                        raw = raw.decode()
                    try:
                        payload = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        logger.exception("failed to decode forwarded payload: %r", raw)
                        continue
                    try:
                        await self._handler(payload)
                    except Exception:
                        logger.exception("forward handler raised on payload session_key=%s",
                                         payload.get("session_key"))
            except asyncio.CancelledError:
                raise
            except Exception:
                if self._stopping:
                    return
                logger.warning(
                    "forward consumer subscription on %s lost; retrying in %.1fs",
                    self.channel, backoff_s, exc_info=True,
                )
                await asyncio.sleep(backoff_s)
                backoff_s = min(backoff_s * 2, max_backoff_s)

    async def start(self) -> None:
        await self._run_loop()

    async def stop(self) -> None:
        self._stopping = True
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(self.channel)
                await self._pubsub.close()
            except Exception:
                logger.exception("error closing pubsub on stop")
        if self._task is not None and not self._task.done():
            self._task.cancel()
