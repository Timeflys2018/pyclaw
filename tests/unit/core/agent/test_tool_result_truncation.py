from __future__ import annotations

from pyclaw.core.agent.tool_result_truncation import (
    resolve_max_output_chars,
    truncate_tool_result,
)
from pyclaw.models import TextBlock, ToolResult


def _make_result(text: str, call_id: str = "c1", is_error: bool = False) -> ToolResult:
    return ToolResult(
        tool_call_id=call_id,
        content=[TextBlock(text=text)],
        is_error=is_error,
    )


class TestTruncateToolResult:
    def test_short_output_passthrough(self) -> None:
        result = _make_result("short")
        out = truncate_tool_result(result, max_chars=100)
        assert out.content[0].text == "short"
        assert len(out.content) == 1

    def test_long_output_truncated(self) -> None:
        result = _make_result("a" * 1000)
        out = truncate_tool_result(result, max_chars=100)
        text_total = "".join(
            b.text for b in out.content if isinstance(b, TextBlock)
        )
        assert text_total.startswith("a" * 100)
        assert "[... 900 more characters truncated]" in text_total

    def test_preserves_call_id_and_error_flag(self) -> None:
        result = _make_result("x" * 500, call_id="abc", is_error=True)
        out = truncate_tool_result(result, max_chars=50)
        assert out.tool_call_id == "abc"
        assert out.is_error is True

    def test_marker_format_exact(self) -> None:
        result = _make_result("x" * 100)
        out = truncate_tool_result(result, max_chars=40)
        marker_block = out.content[-1]
        assert isinstance(marker_block, TextBlock)
        assert marker_block.text.strip() == "[... 60 more characters truncated]"

    def test_utf8_multibyte_preserved_at_codepoint(self) -> None:
        emoji = "😀"
        result = _make_result(emoji * 50)
        out = truncate_tool_result(result, max_chars=10)
        head = out.content[0]
        assert isinstance(head, TextBlock)
        assert head.text == emoji * 10
        for block in out.content:
            assert isinstance(block, TextBlock)

    def test_zero_cap_disables_truncation(self) -> None:
        result = _make_result("a" * 1000)
        out = truncate_tool_result(result, max_chars=0)
        assert out.content[0].text == "a" * 1000

    def test_multiple_text_blocks_truncated_across_boundary(self) -> None:
        result = ToolResult(
            tool_call_id="c1",
            content=[
                TextBlock(text="a" * 50),
                TextBlock(text="b" * 50),
            ],
            is_error=False,
        )
        out = truncate_tool_result(result, max_chars=60)
        joined = "".join(
            b.text for b in out.content if isinstance(b, TextBlock)
        )
        assert "a" * 50 in joined
        assert "b" * 10 in joined
        assert "[... 40 more characters truncated]" in joined


class TestResolveMaxOutputChars:
    def test_no_override_uses_default(self) -> None:
        class _T:
            pass

        assert resolve_max_output_chars(_T(), default_cap=25_000) == 25_000

    def test_override_wins(self) -> None:
        class _T:
            max_output_chars = 100_000

        assert resolve_max_output_chars(_T(), default_cap=25_000) == 100_000

    def test_invalid_override_falls_back(self) -> None:
        class _T:
            max_output_chars = "not a number"

        assert resolve_max_output_chars(_T(), default_cap=25_000) == 25_000

    def test_negative_override_falls_back(self) -> None:
        class _T:
            max_output_chars = -1

        assert resolve_max_output_chars(_T(), default_cap=25_000) == 25_000
