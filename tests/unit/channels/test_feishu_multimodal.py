from __future__ import annotations

import base64
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.channels.feishu.multimodal import (
    _detect_mime_from_magic,
    feishu_image_to_block,
)


def test_png_magic_bytes_detected() -> None:
    raw = b"\x89PNG\r\n\x1a\nfollowed_by_anything"
    assert _detect_mime_from_magic(raw) == "image/png"


def test_jpeg_magic_bytes_detected() -> None:
    raw = b"\xff\xd8\xff\xe0_JFIF_..."
    assert _detect_mime_from_magic(raw) == "image/jpeg"


def test_webp_magic_bytes_detected() -> None:
    raw = b"RIFF\x00\x00\x00\x00WEBP_VP8 _data..."
    assert _detect_mime_from_magic(raw) == "image/webp"


def test_gif_magic_bytes_detected() -> None:
    assert _detect_mime_from_magic(b"GIF87a_data") == "image/gif"
    assert _detect_mime_from_magic(b"GIF89a_data") == "image/gif"


def test_heic_magic_bytes_detected() -> None:
    raw = b"\x00\x00\x00\x18ftypheic_data..."
    assert _detect_mime_from_magic(raw) == "image/heic"


def test_unknown_bytes_default_to_jpeg_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    raw = b"\x00\x01\x02\x03_unknown_format"
    with caplog.at_level(logging.WARNING, logger="pyclaw.channels.feishu.multimodal"):
        mime = _detect_mime_from_magic(raw)
    assert mime == "image/jpeg"
    assert any("unknown image MIME" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_feishu_image_to_block_uses_detected_mime() -> None:
    raw = b"\x89PNG\r\n\x1a\nrest_of_png"
    client = MagicMock()
    client.download_image = AsyncMock(return_value=raw)

    block = await feishu_image_to_block(client, "msg_x", "img_key_x")

    assert block.type == "image"
    assert block.mime_type == "image/png"
    assert block.data == base64.b64encode(raw).decode()
