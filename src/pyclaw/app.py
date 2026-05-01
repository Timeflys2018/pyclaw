from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request

from pyclaw.infra.redis import close_client, get_client, ping
from pyclaw.infra.settings import load_settings
from pyclaw.storage.lock.redis import RedisLockManager
from pyclaw.storage.protocols import SessionStore
from pyclaw.storage.session.factory import create_session_store
from pyclaw.storage.workspace.file import FileWorkspaceStore

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = load_settings()
    redis_client = None

    if settings.storage.session_backend == "redis":
        redis_client = await get_client(settings.redis)
        lock_manager = RedisLockManager(
            redis_client, key_prefix=settings.redis.key_prefix
        )
        app.state.session_store = create_session_store(
            settings.storage,
            redis_client,
            lock_manager,
            ttl_seconds=settings.redis.ttl_seconds,
        )
        app.state.redis_client = redis_client
    else:
        app.state.session_store = create_session_store(settings.storage)
        app.state.redis_client = None

    from pyclaw.core.agent.factory import create_agent_runner_deps

    runner_deps = create_agent_runner_deps(settings, app.state.session_store)
    app.state.runner_deps = runner_deps

    workspace_store = FileWorkspaceStore()
    app.state.workspace_store = workspace_store

    app.state.feishu_channel = None
    if settings.channels.feishu.enabled:
        from pyclaw.channels.feishu.client import FeishuClient
        from pyclaw.channels.feishu.dedup import FeishuDedup
        from pyclaw.channels.feishu.webhook import FeishuChannelPlugin

        dedup = FeishuDedup(redis_client=app.state.redis_client)
        feishu_client = FeishuClient(settings.channels.feishu)
        feishu_channel = FeishuChannelPlugin(
            settings.channels.feishu,
            feishu_client,
            runner_deps,
            dedup,
            workspace_store,
        )
        app.state.feishu_channel = feishu_channel
        asyncio.create_task(feishu_channel.start())
        logger.info("Feishu channel starting...")

    yield

    if app.state.feishu_channel is not None:
        await app.state.feishu_channel.stop()

    if redis_client is not None:
        await close_client(redis_client)


async def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def create_app() -> FastAPI:
    app = FastAPI(title="PyClaw", version="0.1.0", lifespan=_lifespan)

    @app.get("/health")
    async def health(request: Request) -> dict:
        backend = getattr(request.app.state, "session_store", None)
        redis_client = getattr(request.app.state, "redis_client", None)
        result: dict = {"status": "ok", "storage": type(backend).__name__ if backend else "none"}
        if redis_client is not None:
            result["redis"] = "ok" if await ping(redis_client) else "error"

        feishu_channel = getattr(request.app.state, "feishu_channel", None)
        if feishu_channel is None:
            result["feishu"] = "disabled"
        else:
            result["feishu"] = feishu_channel.status

        return result

    return app


def main() -> None:
    import uvicorn

    uvicorn.run("pyclaw.app:create_app", factory=True, host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
