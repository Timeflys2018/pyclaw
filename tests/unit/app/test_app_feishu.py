from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pyclaw.app import create_app


@pytest.fixture(autouse=True)
def no_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_feishu_disabled_when_no_config() -> None:
    app = create_app()
    with TestClient(app) as client:
        feishu_channel = app.state.feishu_channel
    assert feishu_channel is None


def test_health_includes_feishu_disabled() -> None:
    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("feishu") == "disabled"
