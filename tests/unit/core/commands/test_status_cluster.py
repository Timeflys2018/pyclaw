from __future__ import annotations

import types
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.core.commands._helpers import format_session_status
from pyclaw.models import SessionHeader, SessionTree


def _build_tree() -> SessionTree:
    header = SessionHeader(
        id="web:user1:s:abc123",
        workspace_id="default",
        agent_id="default",
    )
    return SessionTree(header=header)


def _build_deps() -> object:
    session_store = types.SimpleNamespace(load=AsyncMock(return_value=_build_tree()))
    llm = types.SimpleNamespace(default_model="m1")
    return types.SimpleNamespace(session_store=session_store, llm=llm)


def _make_worker_registry(*, available: bool, workers: list[dict] | None = None) -> object:
    reg = MagicMock()
    reg.worker_id = "worker:host:1234:abcd"
    reg.available = available
    reg.active_workers = AsyncMock(return_value=workers or [])
    return reg


def _make_gateway_router(owner: str | None, my_worker_id: str = "worker:host:1234:abcd") -> object:
    affinity = MagicMock()
    affinity.resolve = AsyncMock(return_value=owner)
    affinity.is_mine = lambda oid: oid == my_worker_id
    router = MagicMock()
    router.affinity = affinity
    return router


class TestClusterSection:
    @pytest.mark.asyncio
    async def test_no_cluster_when_worker_registry_none(self) -> None:
        output = await format_session_status(
            "user1", "web:user1:s:abc123", _build_deps()
        )
        assert "🏗️" not in output
        assert "Cluster" not in output

    @pytest.mark.asyncio
    async def test_worker_id_shown_when_registry_provided(self) -> None:
        wr = _make_worker_registry(available=False)
        output = await format_session_status(
            "user1", "web:user1:s:abc123", _build_deps(), worker_registry=wr
        )
        assert "🏗️" in output
        assert "worker:host:1234:abcd" in output

    @pytest.mark.asyncio
    async def test_affinity_mine_shown_with_check(self) -> None:
        wr = _make_worker_registry(available=True, workers=[
            {"id": "worker:host:1234:abcd", "status": "healthy", "last_heartbeat": 0, "age_seconds": 5},
        ])
        gr = _make_gateway_router(owner="worker:host:1234:abcd")
        output = await format_session_status(
            "user1", "web:user1:s:abc123", _build_deps(),
            worker_registry=wr, gateway_router=gr,
        )
        assert "本 worker" in output
        assert "✅" in output

    @pytest.mark.asyncio
    async def test_affinity_other_worker_displayed(self) -> None:
        wr = _make_worker_registry(available=True, workers=[
            {"id": "worker:host:1234:abcd", "status": "healthy", "last_heartbeat": 0, "age_seconds": 5},
            {"id": "worker:host:5678:efgh", "status": "healthy", "last_heartbeat": 0, "age_seconds": 5},
        ])
        gr = _make_gateway_router(owner="worker:host:5678:efgh")
        output = await format_session_status(
            "user1", "web:user1:s:abc123", _build_deps(),
            worker_registry=wr, gateway_router=gr,
        )
        assert "worker:host:5678:efgh" in output
        assert "本 worker" not in output

    @pytest.mark.asyncio
    async def test_affinity_unbound_when_no_owner(self) -> None:
        wr = _make_worker_registry(available=True, workers=[
            {"id": "worker:host:1234:abcd", "status": "healthy", "last_heartbeat": 0, "age_seconds": 5},
        ])
        gr = _make_gateway_router(owner=None)
        output = await format_session_status(
            "user1", "web:user1:s:abc123", _build_deps(),
            worker_registry=wr, gateway_router=gr,
        )
        assert "未绑定" in output

    @pytest.mark.asyncio
    async def test_cluster_count_with_stale_and_dead(self) -> None:
        wr = _make_worker_registry(available=True, workers=[
            {"id": "w1", "status": "healthy", "last_heartbeat": 0, "age_seconds": 5},
            {"id": "w2", "status": "healthy", "last_heartbeat": 0, "age_seconds": 5},
            {"id": "w3", "status": "stale", "last_heartbeat": 0, "age_seconds": 100},
            {"id": "w4", "status": "dead", "last_heartbeat": 0, "age_seconds": 200},
        ])
        output = await format_session_status(
            "user1", "web:user1:s:abc123", _build_deps(), worker_registry=wr
        )
        assert "2 健康" in output
        assert "1 stale" in output
        assert "1 dead" in output

    @pytest.mark.asyncio
    async def test_resolve_failure_does_not_break_status(self) -> None:
        wr = _make_worker_registry(available=True, workers=[
            {"id": "w1", "status": "healthy", "last_heartbeat": 0, "age_seconds": 5},
        ])
        gr = _make_gateway_router(owner="w1")
        gr.affinity.resolve = AsyncMock(side_effect=ConnectionError("redis down"))
        output = await format_session_status(
            "user1", "web:user1:s:abc123", _build_deps(),
            worker_registry=wr, gateway_router=gr,
        )
        assert "🏗️" in output
        assert "1 健康" in output
