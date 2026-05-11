from __future__ import annotations

from pyclaw.models.agent import ImageBlock, TextBlock
from pyclaw.models.utils import extract_text_from_content


def test_string_content_returned_as_is() -> None:
    assert extract_text_from_content("hello") == "hello"


def test_empty_string_returns_empty() -> None:
    assert extract_text_from_content("") == ""


def test_none_returns_empty() -> None:
    assert extract_text_from_content(None) == ""


def test_list_of_pydantic_blocks_extracts_text() -> None:
    blocks = [
        TextBlock(type="text", text="hello"),
        TextBlock(type="text", text="world"),
    ]
    assert extract_text_from_content(blocks) == "hello\nworld"


def test_list_with_imageblock_inserts_placeholder() -> None:
    blocks = [
        TextBlock(type="text", text="what is this"),
        ImageBlock(type="image", data="b64", mime_type="image/png"),
    ]
    result = extract_text_from_content(blocks)
    assert "what is this" in result
    assert "[图片]" in result


def test_list_of_dict_form_llm_format() -> None:
    blocks = [
        {"type": "text", "text": "hello"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
    ]
    result = extract_text_from_content(blocks)
    assert "hello" in result
    assert "[图片]" in result


def test_list_of_dict_anthropic_image_form() -> None:
    blocks = [
        {"type": "text", "text": "look"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "..."}},
    ]
    result = extract_text_from_content(blocks)
    assert "look" in result
    assert "[图片]" in result


def test_pure_imageblock_list_returns_only_placeholders() -> None:
    blocks = [
        ImageBlock(type="image", data="b64", mime_type="image/png"),
        ImageBlock(type="image", data="b64", mime_type="image/jpeg"),
    ]
    result = extract_text_from_content(blocks)
    assert result.count("[图片]") == 2


def test_empty_list_returns_empty() -> None:
    assert extract_text_from_content([]) == ""


def test_unknown_type_skipped() -> None:
    blocks = [
        TextBlock(type="text", text="ok"),
        42,
        None,
        {"type": "tool_use", "name": "x"},
    ]
    result = extract_text_from_content(blocks)
    assert result == "ok"
