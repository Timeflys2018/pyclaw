from __future__ import annotations

from typing import Any

from pyclaw.core.context_engine import ContextEngine, DefaultContextEngine


class TestDefaultContextEngineProtocol:
    def test_implements_context_engine(self) -> None:
        assert isinstance(DefaultContextEngine(), ContextEngine)


class TestAssemble:
    async def test_pass_through_messages(self) -> None:
        engine = DefaultContextEngine()
        msgs = [{"role": "user", "content": "hi"}]
        result = await engine.assemble(session_id="s1", messages=msgs)
        assert result.messages == msgs
        assert result.system_prompt_addition is None


class TestIngestAndAfterTurn:
    async def test_ingest_is_noop(self) -> None:
        engine = DefaultContextEngine()
        await engine.ingest("s1", {"role": "user", "content": "x"})

    async def test_after_turn_is_noop(self) -> None:
        engine = DefaultContextEngine()
        await engine.after_turn("s1", [])


class TestCompact:
    async def test_within_budget_does_not_compact(self) -> None:
        engine = DefaultContextEngine()
        msgs = [{"role": "user", "content": "hi"}]
        result = await engine.compact(session_id="s1", messages=msgs, token_budget=100_000)
        assert result.ok is True
        assert result.compacted is False

    async def test_over_budget_compacts_with_fallback_summary(self) -> None:
        engine = DefaultContextEngine(threshold=0.01, keep_recent_tokens=100)
        big = "x" * 10_000
        msgs = [
            {"role": "user", "content": big},
            {"role": "assistant", "content": big},
            {"role": "user", "content": big},
        ]
        result = await engine.compact(session_id="s1", messages=msgs, token_budget=1_000)
        assert result.compacted is True
        assert result.summary is not None
        assert "summary" in result.summary.lower()

    async def test_custom_summarizer_used(self) -> None:
        calls: list[list[dict[str, Any]]] = []

        async def fake_summarize(payload: list[dict[str, Any]]) -> str:
            calls.append(payload)
            return "custom summary"

        engine = DefaultContextEngine(
            threshold=0.01,
            keep_recent_tokens=100,
            summarize=fake_summarize,
        )
        big = "y" * 5_000
        msgs = [{"role": "user", "content": big}, {"role": "assistant", "content": big}]
        result = await engine.compact(session_id="s1", messages=msgs, token_budget=500)
        assert result.compacted is True
        assert result.summary == "custom summary"
        assert len(calls) == 1
        assert calls[0][0]["role"] == "system"


class TestFallbackSummary:
    def test_string_content_preserved(self) -> None:
        from pyclaw.core.context_engine import _fallback_summary

        msgs = [{"role": "user", "content": "hello world"}]
        result = _fallback_summary(msgs)
        assert "hello world" in result

    def test_list_content_with_image_extracts_text_and_placeholder(self) -> None:
        from pyclaw.core.context_engine import _fallback_summary

        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "what is this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]},
        ]
        result = _fallback_summary(msgs)
        assert "what is this" in result
        assert "[图片]" in result

    def test_pure_image_turn_not_silently_dropped(self) -> None:
        from pyclaw.core.context_engine import _fallback_summary

        msgs = [
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,xyz"}},
            ]},
            {"role": "assistant", "content": "I see the image"},
        ]
        result = _fallback_summary(msgs)
        assert "[图片]" in result
        assert "I see the image" in result
