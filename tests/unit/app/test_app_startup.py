from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pyclaw.app import create_app
from pyclaw.storage.session.base import InMemorySessionStore


@pytest.fixture(autouse=True)
def no_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_health_endpoint_returns_ok() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"


def test_session_store_on_app_state_is_in_memory_by_default() -> None:
    app = create_app()
    with TestClient(app) as client:
        store = app.state.session_store
    assert isinstance(store, InMemorySessionStore)


def test_health_includes_storage_type() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
    data = response.json()
    assert "storage" in data
    assert "InMemorySessionStore" in data["storage"]
