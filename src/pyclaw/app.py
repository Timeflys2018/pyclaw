from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

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
    lock_manager: RedisLockManager | None = None

    if settings.storage.session_backend == "redis":
        redis_client = await get_client(settings.redis)
        lock_manager = RedisLockManager(redis_client, key_prefix=settings.redis.key_prefix)
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

    from pyclaw.sandbox import resolve_sandbox_state

    sandbox_state = resolve_sandbox_state(settings.sandbox)
    app.state.sandbox_state = sandbox_state
    app.state.sandbox_policy = sandbox_state.policy
    if sandbox_state.warning:
        logger.warning("sandbox: %s", sandbox_state.warning)

    from pyclaw.core.agent.factory import create_agent_runner_deps

    task_manager = TaskManager(
        default_shutdown_grace_s=float(settings.shutdown_grace_seconds),
    )
    app.state.task_manager = task_manager

    from pyclaw.gateway.worker_registry import WorkerRegistry, generate_worker_id

    worker_id = generate_worker_id()
    worker_registry = WorkerRegistry(
        redis_client=redis_client,
        worker_id=worker_id,
        heartbeat_interval=settings.affinity.heartbeat_interval,
    )
    app.state.worker_registry = worker_registry
    if worker_registry.available:
        await worker_registry.register()
        task_manager.spawn(
            "worker-heartbeat",
            _worker_heartbeat_loop(worker_registry),
            category="heartbeat",
        )
        logger.info("Worker registered: %s", worker_id)

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

    mcp_manager = None
    mcp_death_handler = None
    external_tool_registrar = None
    if settings.mcp.enabled:
        from pyclaw.integrations.mcp.client_manager import MCPClientManager

        mcp_manager = MCPClientManager(settings.mcp, task_manager=task_manager)
        mcp_manager.set_sandbox_policy(app.state.sandbox_policy)
        mcp_manager._emit_sandbox_startup_advisories()
        mcp_manager.start_background()
        mcp_death_handler = mcp_manager._handle_server_death
        external_tool_registrar = mcp_manager.attach_and_register
    app.state.mcp_manager = mcp_manager

    runner_deps = await create_agent_runner_deps(
        settings,
        app.state.session_store,
        workspace_store=workspace_store,
        task_manager=task_manager,
        memory_store=memory_store,
        redis_client=redis_client,
        lock_manager=lock_manager,
        mcp_death_handler=mcp_death_handler,
        external_tool_registrar=external_tool_registrar,
    )
    app.state.runner_deps = runner_deps

    # Task 10.5: expose workspace_base unconditionally for all channels
    workspace_base = Path(settings.workspaces.default).expanduser()
    app.state.workspace_base = workspace_base

    from pyclaw.core.commands.builtin import register_builtin_commands
    from pyclaw.core.commands.registry import get_default_registry, reset_default_registry

    reset_default_registry()
    command_registry = get_default_registry()
    register_builtin_commands(command_registry)
    app.state.command_registry = command_registry

    from pyclaw.channels.web import chat as _web_chat
    from pyclaw.channels.web.websocket import registry as _ws_registry

    _web_chat._session_queue.reset()
    _ws_registry.clear()

    gateway_router = None
    forward_consumer = None
    if settings.affinity.enabled and redis_client is not None:
        from pyclaw.gateway.affinity import AffinityRegistry
        from pyclaw.gateway.forwarder import ForwardConsumer, ForwardPublisher
        from pyclaw.gateway.router import GatewayRouter

        affinity_registry = AffinityRegistry(
            redis_client,
            worker_id=worker_id,
            ttl_seconds=settings.affinity.ttl_seconds,
        )
        forward_publisher = ForwardPublisher(
            redis_client,
            prefix=settings.affinity.forward_prefix,
        )
        gateway_router = GatewayRouter(affinity_registry, forward_publisher)

        async def _dispatch_forwarded_event(payload: dict) -> None:
            ctx_obj = getattr(getattr(app.state, "feishu_channel", None), "_ctx", None)
            if ctx_obj is None:
                logger.warning("forwarded event arrived but feishu ctx not ready; dropping")
                return
            event_payload = payload.get("payload") or {}
            from pyclaw.gateway.event_codec import reconstruct_feishu_event

            try:
                event = reconstruct_feishu_event(event_payload)
            except Exception:
                logger.exception("failed to reconstruct forwarded event")
                return
            from pyclaw.channels.feishu.handler import handle_feishu_message

            await handle_feishu_message(event, ctx_obj)

        forward_consumer = ForwardConsumer(
            redis_client,
            worker_id=worker_id,
            handler_fn=_dispatch_forwarded_event,
            prefix=settings.affinity.forward_prefix,
        )
        task_manager.spawn(
            "forward-consumer",
            forward_consumer.start(),
            category="consumer",
        )
        app.state.gateway_router = gateway_router
        app.state.forward_consumer = forward_consumer
        logger.info("Session affinity gateway enabled (worker=%s)", worker_id)
    else:
        app.state.gateway_router = None
        app.state.forward_consumer = None

    app.state.feishu_channel = None
    if settings.channels.feishu.enabled:
        from pyclaw.channels.feishu.approval_registry import FeishuApprovalRegistry
        from pyclaw.channels.feishu.client import FeishuClient
        from pyclaw.channels.feishu.dedup import FeishuDedup
        from pyclaw.channels.feishu.tool_approval_hook import FeishuToolApprovalHook
        from pyclaw.channels.feishu.webhook import FeishuChannelPlugin
        from pyclaw.infra.audit_logger import AuditLogger

        dedup = FeishuDedup(redis_client=app.state.redis_client)
        feishu_client = FeishuClient(settings.channels.feishu)
        feishu_approval_registry = FeishuApprovalRegistry()
        feishu_audit_logger = AuditLogger()
        feishu_tool_approval_hook = FeishuToolApprovalHook(
            client=feishu_client,
            registry=feishu_approval_registry,
            settings=settings.channels.feishu,
            audit_logger=feishu_audit_logger,
            task_manager=task_manager,
        )
        feishu_channel = FeishuChannelPlugin(
            settings.channels.feishu,
            feishu_client,
            runner_deps,
            dedup,
            workspace_store,
            settings_full=settings,
            bootstrap_files=settings.workspaces.bootstrap_files,
            workspace_base=workspace_base,
            memory_store=memory_store,
            redis_client=redis_client,
            evolution_settings=settings.evolution if settings.evolution.enabled else None,
            agent_settings=settings.agent,
            admin_user_ids=settings.admin_user_ids,
            gateway_router=gateway_router,
            worker_registry=worker_registry,
            tool_approval_hook=feishu_tool_approval_hook,
            approval_registry=feishu_approval_registry,
            audit_logger=feishu_audit_logger,
            mcp_manager=mcp_manager,
            sandbox_policy=app.state.sandbox_policy,
        )
        app.state.feishu_channel = feishu_channel
        await feishu_channel.start()
        logger.info("Feishu channel started")

    if settings.channels.web.enabled:
        from pyclaw.channels.session_router import SessionRouter
        from pyclaw.channels.web.admin import set_admin_registry
        from pyclaw.channels.web.openai_compat import set_openai_deps
        from pyclaw.channels.web.routes import set_web_deps

        app.state.web_settings = settings.channels.web
        app.state.settings = settings

        from pyclaw.core.agent.hooks.memory_nudge_hook import MemoryNudgeHook

        web_nudge_hook = next(
            (h for h in runner_deps.hooks.hooks() if isinstance(h, MemoryNudgeHook)),
            None,
        )

        async def _web_on_rotated(old_session_id: str) -> None:
            if memory_store is None:
                return
            from pyclaw.core.memory_archive import archive_session_background

            archive_owner = old_session_id.split(":s:", 1)[0] if ":s:" in old_session_id else None
            task_manager.spawn(
                f"archive:{old_session_id}",
                archive_session_background(memory_store, app.state.session_store, old_session_id),
                category="archive",
                owner=archive_owner,
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
        set_openai_deps(
            runner_deps,
            session_router,
            workspace_base=workspace_base,
            web_settings=settings.channels.web,
            redis_client=redis_client,
            sandbox_policy=app.state.sandbox_policy,
        )

        set_admin_registry(worker_registry)

        from pyclaw.channels.web.chat import SessionQueue
        from pyclaw.channels.web.deps import WebDeps
        from pyclaw.channels.web.tool_approval_hook import WebToolApprovalHook
        from pyclaw.channels.web.websocket import ConnectionRegistry
        from pyclaw.infra.audit_logger import AuditLogger

        web_session_queue = SessionQueue(task_manager=task_manager)
        web_connection_registry = ConnectionRegistry()
        web_audit_logger = AuditLogger()
        web_tool_approval_hook = WebToolApprovalHook(
            session_queue=web_session_queue,
            settings=settings.channels.web,
            audit_logger=web_audit_logger,
        )
        app.state.web_deps = WebDeps(
            session_store=app.state.session_store,
            session_router=session_router,
            workspace_base=workspace_base,
            runner_deps=runner_deps,
            session_queue=web_session_queue,
            connection_registry=web_connection_registry,
            settings_full=settings,
            redis_client=redis_client,
            memory_store=memory_store,
            task_manager=task_manager,
            evolution_settings=settings.evolution if settings.evolution.enabled else None,
            nudge_hook=web_nudge_hook,
            llm_client=runner_deps.llm,
            agent_settings=settings.agent,
            worker_registry=worker_registry,
            admin_user_ids=settings.admin_user_ids,
            tool_approval_hook=web_tool_approval_hook,
            audit_logger=web_audit_logger,
        )

        logger.info("Web channel enabled (worker=%s)", worker_id)

    if (
        settings.evolution.enabled
        and settings.evolution.curator.enabled
        and redis_client is not None
        and lock_manager is not None
    ):
        from pyclaw.core.curator import create_curator_loop

        memory_base_dir = Path(settings.memory.base_dir).expanduser()
        _l1_index = getattr(memory_store, "_l1", None) if memory_store else None
        if _l1_index is not None:
            task_manager.spawn(
                "curator-scan",
                create_curator_loop(
                    settings=settings.evolution.curator,
                    memory_base_dir=memory_base_dir,
                    redis_client=redis_client,
                    l1_index=_l1_index,
                    lock_manager=lock_manager,
                    task_manager=task_manager,
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

    if getattr(app.state, "mcp_manager", None) is not None:
        try:
            await app.state.mcp_manager.shutdown()
        except Exception:
            logger.exception("mcp_manager.shutdown failed")

    # Phase 2: Drain background tasks
    report = await task_manager.shutdown()
    logger.info(
        "shutdown drain complete: completed=%d cancelled=%d timed_out=%d failed=%d duration=%.2fs",
        report.completed,
        report.cancelled,
        report.timed_out,
        report.failed,
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
    _install_access_log_filter()
    settings = load_settings()
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

        mcp_manager = getattr(request.app.state, "mcp_manager", None)
        if mcp_manager is not None:
            summary = mcp_manager.connection_summary()
            result["mcp"] = {
                "ready": mcp_manager.is_ready(),
                "n_connected": summary.n_connected,
                "n_failed": summary.n_failed,
                "n_pending": summary.n_pending,
                "n_disabled": summary.n_disabled,
                "total_tools": summary.total_tools,
            }

        sandbox_state = getattr(request.app.state, "sandbox_state", None)
        if sandbox_state is not None:
            from pyclaw.sandbox import health_advisory

            result["sandbox"] = health_advisory(sandbox_state)

        worker_registry = getattr(request.app.state, "worker_registry", None)
        if worker_registry is not None:
            result["worker_id"] = worker_registry.worker_id
            if worker_registry.available:
                workers = await worker_registry.active_workers()
                result["cluster_size"] = sum(1 for w in workers if w["status"] == "healthy")

        return result

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

    return app


def _install_access_log_filter() -> None:
    """Filter uvicorn's access log to drop noisy paths (static assets, favicon).

    HTTP API + WebSocket requests still log; everything served from
    `web/dist/` is hidden so the dev console reflects backend activity only.
    Idempotent via duck-typing: safe to call from multiple entry points
    (main() and create_app()) and across uvicorn --reload reimports.
    """
    import logging
    import re

    access_logger = logging.getLogger("uvicorn.access")
    for existing in access_logger.filters:
        if getattr(existing, "_pyclaw_static_filter", False):
            return

    silent = re.compile(r' "(GET|HEAD) /(assets/|favicon\.|@vite/|@react-refresh|src/)')

    class _DropStaticAssets(logging.Filter):
        _pyclaw_static_filter = True

        def filter(self, record: logging.LogRecord) -> bool:
            try:
                msg = record.getMessage()
            except Exception:
                return True
            return silent.search(msg) is None

    access_logger.addFilter(_DropStaticAssets())


def main() -> None:
    import uvicorn

    settings = load_settings()
    port = int(os.environ.get("PORT", settings.server.port))
    host = os.environ.get("HOST", settings.server.host)
    _install_access_log_filter()
    uvicorn.run("pyclaw.app:create_app", factory=True, host=host, port=port, reload=True)


if __name__ == "__main__":
    main()
