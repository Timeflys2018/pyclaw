from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

from fastapi import FastAPI, Request

from pyclaw.infra.redis import close_client, get_client, ping
from pyclaw.infra.settings import load_settings
from pyclaw.infra.task_manager import TaskManager
from pyclaw.storage.lock.redis import RedisLockManager
from pyclaw.storage.protocols import SessionStore
from pyclaw.storage.session.factory import create_session_store
from pyclaw.storage.workspace.factory import create_workspace_store

logger = logging.getLogger(__name__)

import litellm

litellm.suppress_debug_info = True


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    if not logging.root.handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")

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

    task_manager = TaskManager(
        default_shutdown_grace_s=float(settings.shutdown_grace_seconds),
    )
    app.state.task_manager = task_manager

    workspace_store = create_workspace_store(settings, redis_client=redis_client)
    app.state.workspace_store = workspace_store

    memory_store = None
    if redis_client is not None:
        try:
            from pyclaw.storage.memory.factory import create_memory_store

            memory_store = create_memory_store(
                settings.storage,
                settings.memory,
                settings.embedding,
                redis_client,
            )
        except Exception:
            logger.exception("failed to create memory_store; agent will run without memory")
            memory_store = None
    app.state.memory_store = memory_store

    runner_deps = await create_agent_runner_deps(
        settings, app.state.session_store,
        workspace_store=workspace_store,
        task_manager=task_manager,
        memory_store=memory_store,
        redis_client=redis_client,
    )
    app.state.runner_deps = runner_deps

    # Task 10.5: expose workspace_base unconditionally for all channels
    workspace_base = Path(settings.workspaces.default).expanduser()
    app.state.workspace_base = workspace_base

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
            bootstrap_files=settings.workspaces.bootstrap_files,
            workspace_base=workspace_base,
            memory_store=memory_store,
            redis_client=redis_client,
            evolution_settings=settings.evolution if settings.evolution.enabled else None,
        )
        app.state.feishu_channel = feishu_channel
        await feishu_channel.start()
        logger.info("Feishu channel started")

    app.state.worker_registry = None
    if settings.channels.web.enabled:
        from pyclaw.channels.session_router import SessionRouter
        from pyclaw.channels.web.admin import admin_router, set_admin_registry
        from pyclaw.channels.web.auth_routes import auth_router
        from pyclaw.channels.web.openai_compat import openai_router, set_openai_deps
        from pyclaw.channels.web.routes import set_web_deps, web_router
        from pyclaw.channels.web.websocket import ws_router
        from pyclaw.gateway.worker_registry import WorkerRegistry

        app.state.web_settings = settings.channels.web

        from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook

        web_nudge_hook = next(
            (h for h in runner_deps.hooks.hooks() if isinstance(h, MemoryNudgeHook)),
            None,
        )

        async def _web_on_rotated(old_session_id: str) -> None:
            if memory_store is None:
                return
            from pyclaw.core.memory_archive import archive_session_background

            task_manager.spawn(
                f"archive:{old_session_id}",
                archive_session_background(memory_store, app.state.session_store, old_session_id),
                category="archive",
            )
            if redis_client is not None and settings.evolution.enabled:
                from pyclaw.core.sop_extraction import maybe_spawn_extraction

                await maybe_spawn_extraction(
                    task_manager=task_manager,
                    memory_store=memory_store,
                    session_store=app.state.session_store,
                    redis_client=redis_client,
                    llm_client=runner_deps.llm,
                    session_id=old_session_id,
                    settings=settings.evolution,
                    nudge_hook=web_nudge_hook,
                )

        # TODO: Web channel does not currently support idle-reset session rotation.
        # Sessions only rotate on explicit POST /sessions.
        # Self-evolution will only fire when user creates a new session.
        session_router = SessionRouter(
            store=app.state.session_store,
            on_session_rotated=_web_on_rotated,
        )
        set_web_deps(
            store=app.state.session_store,
            session_router=session_router,
            memory_store=memory_store,
            task_manager=task_manager,
            redis_client=redis_client,
            llm_client=runner_deps.llm,
            evolution_settings=settings.evolution if settings.evolution.enabled else None,
            nudge_hook=web_nudge_hook,
        )
        set_openai_deps(runner_deps, session_router, workspace_base=workspace_base)

        worker_id = f"worker-{id(app)}"
        worker_registry = WorkerRegistry(
            redis_client=redis_client,
            worker_id=worker_id,
            heartbeat_interval=settings.channels.web.heartbeat_interval,
        )
        app.state.worker_registry = worker_registry
        set_admin_registry(worker_registry)

        await worker_registry.register()
        task_manager.spawn(
            "worker-heartbeat",
            _worker_heartbeat_loop(worker_registry),
            category="heartbeat",
        )

        logger.info("Web channel enabled (worker=%s)", worker_id)

    # Curator background loop
    if settings.evolution.enabled and settings.evolution.curator.enabled and redis_client is not None:
        from pyclaw.core.curator import create_curator_loop

        memory_base_dir = Path(settings.memory.base_dir).expanduser()
        _l1_index = getattr(memory_store, '_l1', None) if memory_store else None
        if _l1_index is not None:
            task_manager.spawn(
                "curator",
                create_curator_loop(
                    settings=settings.evolution.curator,
                    memory_base_dir=memory_base_dir,
                    redis_client=redis_client,
                    l1_index=_l1_index,
                    workspace_base_dir=workspace_base,
                    llm_client=runner_deps.llm,
                ),
                category="curator",
            )
            logger.info("Curator background loop started")

    yield

    # Phase 1: Stop accepting new work
    if app.state.worker_registry is not None:
        await app.state.worker_registry.deregister()
    if app.state.feishu_channel is not None:
        await app.state.feishu_channel.stop()

    # Phase 2: Drain background tasks
    report = await task_manager.shutdown()
    logger.info(
        "shutdown drain complete: completed=%d cancelled=%d timed_out=%d failed=%d duration=%.2fs",
        report.completed, report.cancelled, report.timed_out, report.failed,
        report.total_duration_s,
    )

    # Phase 3: Close memory_store (depends on redis; close before redis)
    memory_store = getattr(app.state, "memory_store", None)
    if memory_store is not None:
        try:
            await memory_store.close()
        except Exception:
            logger.warning("memory_store close failed", exc_info=True)

    # Phase 4: Close storage and infrastructure
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
