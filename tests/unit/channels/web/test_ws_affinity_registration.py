from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _build_gateway_router() -> SimpleNamespace:
    affinity = SimpleNamespace(force_claim=AsyncMock())
    return SimpleNamespace(affinity=affinity)


class TestWsAffinityCall:
    @pytest.mark.asyncio
    async def test_force_claim_called_with_correct_session_key(self) -> None:
        gateway = _build_gateway_router()
        await gateway.affinity.force_claim("web:user1")
        gateway.affinity.force_claim.assert_called_once_with("web:user1")

    @pytest.mark.asyncio
    async def test_session_key_format_is_web_userid(self) -> None:
        gateway = _build_gateway_router()
        for user_id in ("alice", "bob", "user-with-dashes", "u_123"):
            await gateway.affinity.force_claim(f"web:{user_id}")
        calls = [c.args[0] for c in gateway.affinity.force_claim.call_args_list]
        assert calls == ["web:alice", "web:bob", "web:user-with-dashes", "web:u_123"]


class TestWsEndpointSourceContract:
    def test_websocket_endpoint_imports_and_uses_force_claim(self) -> None:
        import inspect

        import pyclaw.channels.web.websocket as ws_module

        source = inspect.getsource(ws_module.websocket_endpoint)

        assert "gateway_router" in source, (
            "websocket_endpoint must read gateway_router from app.state"
        )
        assert "force_claim" in source, "websocket_endpoint must call force_claim on connect"
        assert 'f"web:{user_id}"' in source, "session key for web channel must be 'web:{user_id}'"

    def test_force_claim_failure_is_caught(self) -> None:
        import inspect

        import pyclaw.channels.web.websocket as ws_module

        source = inspect.getsource(ws_module.websocket_endpoint)
        force_claim_idx = source.find("force_claim")
        assert force_claim_idx > 0
        assert "try:" in source[:force_claim_idx], (
            "force_claim must be wrapped in try/except so a Redis blip "
            "doesn't drop the WS connection"
        )
        assert "except Exception" in source[force_claim_idx : force_claim_idx + 500], (
            "force_claim failure must be caught (Y6: graceful Redis degradation)"
        )
