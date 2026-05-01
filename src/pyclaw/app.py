from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request

from pyclaw.infra.redis import close_client, get_client, ping
from pyclaw.infra.settings import load_settings
from pyclaw.storage.lock.redis import RedisLockManager
from pyclaw.storage.protocols import SessionStore
from pyclaw.storage.session.factory import create_session_store


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

    yield

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
        return result

    return app


def main() -> None:
    import uvicorn

    uvicorn.run("pyclaw.app:create_app", factory=True, host="0.0.0.0", port=8000, reload=True)


if __name__ == "__main__":
    main()
