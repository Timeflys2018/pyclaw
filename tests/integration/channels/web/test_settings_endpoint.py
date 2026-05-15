from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from pyclaw.channels.web.routes import web_router
from pyclaw.infra.settings import Settings, WebSettings


def _make_app(*, web_settings: WebSettings | None = None) -> FastAPI:
    app = FastAPI()
    app.include_router(web_router)
    web_deps = MagicMock()
    web_deps.settings_full = MagicMock(spec=Settings)
    web_deps.settings_full.channels = MagicMock()
    web_deps.settings_full.channels.web = web_settings or WebSettings()
    app.state.web_deps = web_deps
    return app


class TestSettingsEndpoint:
    def test_returns_default_tier_and_timeout(self) -> None:
        ws = WebSettings(
            defaultPermissionTier="approval",
            toolApprovalTimeoutSeconds=60,
            toolsRequiringApproval=["bash", "write", "edit"],
        )
        client = TestClient(_make_app(web_settings=ws))
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["default_permission_tier"] == "approval"
        assert data["tool_approval_timeout_seconds"] == 60
        assert data["tools_requiring_approval"] == ["bash", "write", "edit"]

    def test_returns_overridden_tier_and_list(self) -> None:
        ws = WebSettings(
            defaultPermissionTier="yolo",
            toolApprovalTimeoutSeconds=120,
            toolsRequiringApproval=["bash"],
        )
        client = TestClient(_make_app(web_settings=ws))
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        data = resp.json()
        assert data["default_permission_tier"] == "yolo"
        assert data["tool_approval_timeout_seconds"] == 120
        assert data["tools_requiring_approval"] == ["bash"]

    def test_does_not_leak_secrets(self) -> None:
        ws = WebSettings(
            jwt_secret="super-secret-do-not-leak",
            admin_token="ADMIN-DO-NOT-LEAK",
        )
        client = TestClient(_make_app(web_settings=ws))
        resp = client.get("/api/settings")
        assert resp.status_code == 200
        body = resp.text
        assert "super-secret-do-not-leak" not in body
        assert "ADMIN-DO-NOT-LEAK" not in body
        assert "jwt_secret" not in body.lower() or "jwt_secret" not in resp.json()
        assert "admin_token" not in body.lower() or "admin_token" not in resp.json()

    def test_no_auth_required(self) -> None:
        ws = WebSettings(defaultPermissionTier="read-only")
        client = TestClient(_make_app(web_settings=ws))
        resp = client.get("/api/settings")
        assert resp.status_code == 200

    def test_returns_500_when_web_deps_missing(self) -> None:
        app = FastAPI()
        app.include_router(web_router)
        client = TestClient(app)
        resp = client.get("/api/settings")
        assert resp.status_code == 500
