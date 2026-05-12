"""Verify owner= is threaded through 6 user-scoped spawn sites + 3 system sites (Phase A2)."""

from __future__ import annotations

import inspect


def test_web_chat_spawn_carries_owner() -> None:
    import pyclaw.channels.web.chat as module

    src = inspect.getsource(module)
    assert "owner=conversation_id" in src


def test_web_websocket_heartbeat_carries_owner() -> None:
    import pyclaw.channels.web.websocket as module

    src = inspect.getsource(module)
    assert 'owner=f"web:{user_id}"' in src
    assert 'owner=f"web:{state.user_id}"' in src


def test_feishu_queue_forwards_owner() -> None:
    import pyclaw.channels.feishu.queue as module

    src = inspect.getsource(module)
    assert "owner: str | None = None" in src
    assert "owner=owner" in src


def test_feishu_handler_passes_session_key_as_owner() -> None:
    import pyclaw.channels.feishu.handler as module

    src = inspect.getsource(module)
    assert "queue_registry.enqueue(session_id, _run(), owner=session_key)" in src


def test_app_web_on_rotated_carries_archive_owner() -> None:
    import pyclaw.app as module

    src = inspect.getsource(module)
    assert 'archive_owner = old_session_id.split(":s:", 1)[0]' in src
    assert "owner=archive_owner" in src


def test_feishu_webhook_on_rotated_carries_archive_owner() -> None:
    import pyclaw.channels.feishu.webhook as module

    src = inspect.getsource(module)
    assert "archive_owner" in src
    assert "owner=archive_owner" in src


def test_system_tasks_do_not_pass_owner_kwarg() -> None:
    import pyclaw.app as app_module
    import pyclaw.core.curator as curator_module

    app_src = inspect.getsource(app_module)
    worker_heartbeat_idx = app_src.find('"worker-heartbeat"')
    if worker_heartbeat_idx >= 0:
        spawn_block = app_src[worker_heartbeat_idx : worker_heartbeat_idx + 600]
        assert "owner=" not in spawn_block, (
            "worker-heartbeat is a system task and SHOULD NOT receive owner="
        )

    curator_src = inspect.getsource(curator_module)
    heartbeat_spawn_idx = curator_src.find('"curator-heartbeat"')
    if heartbeat_spawn_idx >= 0:
        spawn_block = curator_src[heartbeat_spawn_idx : heartbeat_spawn_idx + 400]
        assert "owner=" not in spawn_block, (
            "curator-heartbeat is a system task and SHOULD NOT receive owner="
        )
