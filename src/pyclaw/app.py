from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request

from pyclaw.infra.redis import close_client, get_client, ping
from pyclaw.infra.settings import load_settings
from pyclaw.storage.lock.redis import RedisLockManager
from pyclaw.storage.protocols import SessionStore
from pyclaw.storage.session.factory import create_session_store
from pyclaw.storage.workspace.factory import create_workspace_store

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

    workspace_store = create_workspace_store(settings, redis_client=redis_client)
    app.state.workspace_store = workspace_store

    runner_deps = create_agent_runner_deps(settings, app.state.session_store, workspace_store=workspace_store)
    app.state.runner_deps = runner_deps

    app.state.feishu_channel = None
    if settings.channels.feishu.enabled:
        from pyclaw.channels.feishu.client import FeishuClient
        from pyclaw.channels.feishu.dedup import FeishuDedup
        from pyclaw.channels.feishu.webhook import FeishuChannelPlugin

        dedup = FeishuDedup(redis_client=app.state.redis_client)
        feishu_client = FeishuClient(settings.channels.feishu)
        workspace_base = Path(settings.workspaces.default).expanduser()
        feishu_channel = FeishuChannelPlugin(
            settings.channels.feishu,
            feishu_client,
            runner_deps,
            dedup,
            workspace_store,
            bootstrap_files=settings.workspaces.bootstrap_files,
            workspace_base=workspace_base,
        )
        app.state.feishu_channel = feishu_channel
        asyncio.create_task(feishu_channel.start())
        logger.info("Feishu channel starting...")

    app.state.worker_registry = None
    heartbeat_task = None
    if settings.channels.web.enabled:
        from pyclaw.channels.session_router import SessionRouter
        from pyclaw.channels.web.admin import admin_router, set_admin_registry
        from pyclaw.channels.web.auth_routes import auth_router
        from pyclaw.channels.web.openai_compat import openai_router, set_openai_deps
        from pyclaw.channels.web.routes import set_web_deps, web_router
        from pyclaw.channels.web.websocket import ws_router
        from pyclaw.gateway.worker_registry import WorkerRegistry

        app.state.web_settings = settings.channels.web

        session_router = SessionRouter(store=app.state.session_store)
        set_web_deps(store=app.state.session_store, session_router=session_router)
        set_openai_deps(runner_deps, session_router)

        worker_id = f"worker-{id(app)}"
        worker_registry = WorkerRegistry(
            redis_client=redis_client,
            worker_id=worker_id,
            heartbeat_interval=settings.channels.web.heartbeat_interval,
        )
        app.state.worker_registry = worker_registry
        set_admin_registry(worker_registry)

        await worker_registry.register()
        heartbeat_task = asyncio.create_task(
            _worker_heartbeat_loop(worker_registry)
        )

        logger.info("Web channel enabled (worker=%s)", worker_id)

    yield

    if heartbeat_task is not None:
        heartbeat_task.cancel()
    if app.state.worker_registry is not None:
        await app.state.worker_registry.deregister()

    if app.state.feishu_channel is not None:
        await app.state.feishu_channel.stop()

    if redis_client is not None:
        await close_client(redis_client)


async def _worker_heartbeat_loop(registry) -> None:
    try:
        while True:
            await asyncio.sleep(registry._heartbeat_interval)
            try:
                await registry.heartbeat()
            except Exception:
                logger.warning("Worker heartbeat failed", exc_info=True)
    except asyncio.CancelledError:
        return


async def get_session_store(request: Request) -> SessionStore:
    return request.app.state.session_store


def create_app() -> FastAPI:
    settings = load_settings()
    app = FastAPI(title="PyClaw", version="0.1.0", lifespan=_lifespan)

    if settings.channels.web.enabled:
        from starlette.middleware.cors import CORSMiddleware

        from pyclaw.channels.web.admin import admin_router
        from pyclaw.channels.web.auth_routes import auth_router
        from pyclaw.channels.web.openai_compat import openai_router
        from pyclaw.channels.web.routes import web_router
        from pyclaw.channels.web.websocket import ws_router

        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.channels.web.cors_origins,
            allow_methods=["*"],
            allow_headers=["*"],
        )

        app.include_router(auth_router)
        app.include_router(web_router)
        app.include_router(ws_router)
        app.include_router(openai_router)
        app.include_router(admin_router)

        spa_dir = Path(__file__).resolve().parent.parent.parent / "web" / "dist"
        if spa_dir.is_dir():
            from pyclaw.channels.web.spa import SPAStaticFiles

            app.mount("/", SPAStaticFiles(directory=str(spa_dir), html=True), name="spa")

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
