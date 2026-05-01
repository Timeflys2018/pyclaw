from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from pyclaw.channels.web.auth import verify_admin_token
from pyclaw.gateway.worker_registry import WorkerRegistry

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])

_registry: WorkerRegistry | None = None


def set_admin_registry(registry: WorkerRegistry | None) -> None:
    global _registry
    _registry = registry


@admin_router.get("/cluster")
async def get_cluster(
    request: Request,
    _: None = Depends(verify_admin_token),
) -> dict:
    workers = await _registry.active_workers() if _registry else []
    return {
        "workers": workers,
        "current_worker": _registry.worker_id if _registry else "unknown",
        "total_workers": len([w for w in workers if w.get("status") != "dead"]),
    }
