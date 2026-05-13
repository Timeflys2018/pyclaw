from __future__ import annotations

import time

import pytest

from pyclaw.channels.feishu.client import FeishuClient
from pyclaw.infra.settings import FeishuSettings


@pytest.fixture
def client() -> FeishuClient:
    settings = FeishuSettings(appId="test_app", appSecret="test_secret")
    return FeishuClient(settings)


def test_bot_message_tracking_roundtrip(client: FeishuClient) -> None:
    client._track_bot_message("om_bot_1")
    assert client.is_bot_message("om_bot_1") is True


def test_unknown_message_is_not_bot(client: FeishuClient) -> None:
    assert client.is_bot_message("om_unknown") is False


def test_bot_message_expires_after_ttl(client: FeishuClient) -> None:
    client._track_bot_message("om_bot_2")
    client._bot_sent_message_ids["om_bot_2"] = time.time() - 3601
    assert client.is_bot_message("om_bot_2") is False
    assert "om_bot_2" not in client._bot_sent_message_ids


def test_bot_message_tracking_scoped_per_id(client: FeishuClient) -> None:
    client._track_bot_message("om_a")
    client._track_bot_message("om_b")
    assert client.is_bot_message("om_a") is True
    assert client.is_bot_message("om_b") is True
    assert client.is_bot_message("om_c") is False


def test_bot_message_tracking_gc_when_over_5000(client: FeishuClient) -> None:
    now = time.time()
    old_cutoff = now - 3600 - 1
    for i in range(5001):
        client._bot_sent_message_ids[f"om_old_{i}"] = old_cutoff
    client._track_bot_message("om_fresh")
    assert "om_fresh" in client._bot_sent_message_ids
    assert len(client._bot_sent_message_ids) < 5001
