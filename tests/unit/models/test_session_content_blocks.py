from __future__ import annotations

from pyclaw.models.agent import ImageBlock, TextBlock
from pyclaw.models.session import _content_blocks_to_llm


def test_empty_text_block_skipped() -> None:
    result = _content_blocks_to_llm([TextBlock(type="text", text="")])
    assert result == []


def test_whitespace_only_text_block_skipped() -> None:
    result = _content_blocks_to_llm([TextBlock(type="text", text="   \t\n ")])
    assert result == []


def test_non_empty_text_block_preserved() -> None:
    result = _content_blocks_to_llm([TextBlock(type="text", text="hello")])
    assert result == [{"type": "text", "text": "hello"}]


def test_image_block_serialized_to_image_url() -> None:
    result = _content_blocks_to_llm(
        [ImageBlock(type="image", data="b64data", mime_type="image/png")]
    )
    assert result == [
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,b64data"},
        }
    ]


def test_image_with_empty_text_keeps_image_only() -> None:
    result = _content_blocks_to_llm(
        [
            ImageBlock(type="image", data="b64", mime_type="image/jpeg"),
            TextBlock(type="text", text=""),
        ]
    )
    assert len(result) == 1
    assert result[0]["type"] == "image_url"
