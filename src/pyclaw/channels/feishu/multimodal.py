from __future__ import annotations

import base64
import logging

from pyclaw.models import ImageBlock

logger = logging.getLogger(__name__)


def _detect_mime_from_magic(raw: bytes) -> str:
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and len(raw) >= 12 and raw[8:12] == b"WEBP":
        return "image/webp"
    if len(raw) >= 12 and raw[4:8] == b"ftyp" and raw[8:12] in (b"heic", b"heif", b"heix"):
        return "image/heic"
    logger.warning(
        "unknown image MIME, defaulting to jpeg, magic=%r",
        raw[:16].hex() if raw else "",
    )
    return "image/jpeg"


async def feishu_image_to_block(
    client: object,
    message_id: str,
    image_key: str,
) -> ImageBlock:
    raw: bytes = await client.download_image(message_id, image_key)  # type: ignore[attr-defined]
    mime = _detect_mime_from_magic(raw)
    logger.debug(
        "feishu_image_to_block: image_key=%s size=%d mime=%s magic=%s",
        image_key, len(raw), mime, raw[:8].hex(),
    )
    b64 = base64.b64encode(raw).decode()
    return ImageBlock(type="image", data=b64, mime_type=mime)
