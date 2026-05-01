from __future__ import annotations

import base64

from pyclaw.models import ImageBlock


async def feishu_image_to_block(
    client: object,
    message_id: str,
    image_key: str,
) -> ImageBlock:
    raw: bytes = await client.download_image(message_id, image_key)  # type: ignore[attr-defined]
    b64 = base64.b64encode(raw).decode()
    return ImageBlock(type="image", data=b64, mime_type="image/jpeg")
