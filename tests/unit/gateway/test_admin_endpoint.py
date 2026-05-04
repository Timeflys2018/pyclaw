from __future__ import annotations

import time
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from pyclaw.channels.web.admin import admin_router, set_admin_registry
from pyclaw.gateway.worker_registry import WorkerRegistry
from pyclaw.infra.settings import WebSettings


ADMIN_TOKEN = "test-admin-secret"


def _make_app(registry: WorkerRegistry | None = None) -> FastAPI:
    app = FastAPI()
    settings = WebSettings(admin_token=ADMIN_TOKEN, jwt_secret="test")
    app.state.web_settings = settings
    app.include_router(admin_router)
    if registry:
        set_admin_registry(registry)
    return app


class TestClusterEndpoint:
    def test_valid_admin_token_returns_cluster(self) -> None:
        reg = WorkerRegistry(worker_id="w1")
        app = _make_app(reg)
        client = TestClient(app)
        resp = client.get(
            "/api/admin/cluster",
            headers={"x-admin-token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "workers" in data
        assert data["current_worker"] == "w1"
        assert data["total_workers"] >= 1

    def test_missing_admin_token_returns_403(self) -> None:
        reg = WorkerRegistry(worker_id="w1")
        app = _make_app(reg)
        client = TestClient(app)
        resp = client.get("/api/admin/cluster")
        assert resp.status_code == 403

    def test_wrong_admin_token_returns_403(self) -> None:
        reg = WorkerRegistry(worker_id="w1")
        app = _make_app(reg)
        client = TestClient(app)
        resp = client.get(
            "/api/admin/cluster",
            headers={"x-admin-token": "wrong-token"},
        )
        assert resp.status_code == 403

    def test_no_admin_token_configured_returns_403(self) -> None:
        app = FastAPI()
        settings = WebSettings(admin_token="", jwt_secret="test")
        app.state.web_settings = settings
        app.include_router(admin_router)
        reg = WorkerRegistry(worker_id="w1")
        set_admin_registry(reg)
        client = TestClient(app)
        resp = client.get(
            "/api/admin/cluster",
            headers={"x-admin-token": "anything"},
        )
        assert resp.status_code == 403

    def test_no_registry_returns_empty_workers(self) -> None:
        set_admin_registry(None)
        app = _make_app(registry=None)
        client = TestClient(app)
        resp = client.get(
            "/api/admin/cluster",
            headers={"x-admin-token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["workers"] == []
        assert data["current_worker"] == "unknown"

    def test_filters_dead_workers_from_total(self) -> None:
        redis = AsyncMock()
        now = time.time()
        redis.zrangebyscore.return_value = [
            ("w1", now - 5),
            ("w2", now - 140),
        ]
        reg = WorkerRegistry(redis_client=redis, worker_id="w1")
        app = _make_app(reg)
        client = TestClient(app)
        resp = client.get(
            "/api/admin/cluster",
            headers={"x-admin-token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_workers"] == 1
