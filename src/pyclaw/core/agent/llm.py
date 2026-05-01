from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any


class LLMErrorCode:
    CONTEXT_OVERFLOW = "context_overflow"
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


class LLMError(Exception):
    def __init__(self, code: str, message: str, original: Exception | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.original = original


@dataclass
class LLMUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


@dataclass
class LLMStreamChunk:
    text_delta: str = ""
    tool_call_deltas: list[dict[str, Any]] = field(default_factory=list)
    finish_reason: str | None = None
    usage: LLMUsage | None = None


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict[str, Any]]
    usage: LLMUsage
    finish_reason: str | None


def classify_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "context" in message and ("overflow" in message or "length" in message or "maximum" in message):
        return LLMErrorCode.CONTEXT_OVERFLOW
    if "rate limit" in message or "too many requests" in message or "429" in message:
        return LLMErrorCode.RATE_LIMIT
    if "auth" in message or "api key" in message or "unauthorized" in message or "401" in message:
        return LLMErrorCode.AUTH_ERROR
    if "timeout" in message or "timed out" in message:
        return LLMErrorCode.TIMEOUT
    return LLMErrorCode.UNKNOWN


class LLMClient:
    def __init__(
        self,
        default_model: str = "gpt-4o-mini",
        api_key: str | None = None,
        api_base: str | None = None,
    ) -> None:
        self.default_model = default_model
        self._api_key = api_key
        self._api_base = api_base

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        idle_seconds: float = 0.0,
        abort_event: asyncio.Event | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        from litellm import acompletion

        from pyclaw.core.agent.runtime_util import (
            AgentAbortedError,
            AgentTimeoutError,
            iterate_with_idle_timeout,
        )

        effective_model = model or self.default_model
        payload_messages = _prepend_system(messages, system)

        extra: dict[str, Any] = {}
        if self._api_key:
            extra["api_key"] = self._api_key
        if self._api_base:
            extra["api_base"] = self._api_base

        try:
            stream = await acompletion(
                model=effective_model,
                messages=payload_messages,
                tools=tools or None,
                stream=True,
                stream_options={"include_usage": True},
                **extra,
            )
        except Exception as exc:
            raise LLMError(classify_error(exc), str(exc), original=exc) from exc

        async def _raw_iter() -> AsyncIterator[LLMStreamChunk]:
            async for raw_chunk in stream:
                chunk = _convert_chunk(raw_chunk)
                if chunk is not None:
                    yield chunk

        if idle_seconds <= 0 and abort_event is None:
            async for chunk in _raw_iter():
                yield chunk
            return

        try:
            async for chunk in iterate_with_idle_timeout(
                _raw_iter(),
                idle_seconds=idle_seconds,
                abort_event=abort_event,
                kind="idle",
            ):
                yield chunk
        except AgentTimeoutError as te:
            raise LLMError(LLMErrorCode.TIMEOUT, f"idle timeout after {te.limit_seconds}s") from te
        except AgentAbortedError as ae:
            raise LLMError("aborted", "llm stream aborted") from ae

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        system: str | None = None,
        idle_seconds: float = 0.0,
        abort_event: asyncio.Event | None = None,
    ) -> LLMResponse:
        text_parts: list[str] = []
        tool_calls_buffer: dict[int, dict[str, Any]] = {}
        finish_reason: str | None = None
        usage = LLMUsage()

        async for chunk in self.stream(
            messages=messages,
            model=model,
            tools=tools,
            system=system,
            idle_seconds=idle_seconds,
            abort_event=abort_event,
        ):
            if chunk.text_delta:
                text_parts.append(chunk.text_delta)
            if chunk.tool_call_deltas:
                merge_tool_call_deltas(tool_calls_buffer, chunk.tool_call_deltas)
            if chunk.finish_reason:
                finish_reason = chunk.finish_reason
            if chunk.usage:
                usage = chunk.usage

        tool_calls = finalize_tool_calls(tool_calls_buffer)
        return LLMResponse(
            text="".join(text_parts),
            tool_calls=tool_calls,
            usage=usage,
            finish_reason=finish_reason,
        )


def _prepend_system(messages: list[dict[str, Any]], system: str | None) -> list[dict[str, Any]]:
    if not system:
        return messages
    return [{"role": "system", "content": system}, *messages]


def _convert_chunk(raw: Any) -> LLMStreamChunk | None:
    try:
        choices = getattr(raw, "choices", None) or raw.get("choices")
    except Exception:
        return None
    if not choices:
        usage = _extract_usage(raw)
        if usage is not None:
            return LLMStreamChunk(usage=usage)
        return None

    choice = choices[0]
    delta = _get(choice, "delta") or {}
    text_delta = _get(delta, "content") or ""
    tool_call_deltas = _get(delta, "tool_calls") or []
    finish_reason = _get(choice, "finish_reason")

    normalized_deltas = [_normalize_tool_call_delta(d) for d in tool_call_deltas]
    return LLMStreamChunk(
        text_delta=text_delta or "",
        tool_call_deltas=normalized_deltas,
        finish_reason=finish_reason,
        usage=_extract_usage(raw),
    )


def _extract_usage(raw: Any) -> LLMUsage | None:
    usage = _get(raw, "usage")
    if usage is None:
        return None
    prompt = _get(usage, "prompt_tokens") or _get(usage, "input_tokens") or 0
    completion = _get(usage, "completion_tokens") or _get(usage, "output_tokens") or 0
    total = _get(usage, "total_tokens") or (prompt + completion)
    return LLMUsage(input_tokens=int(prompt), output_tokens=int(completion), total_tokens=int(total))


def _get(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _normalize_tool_call_delta(delta: Any) -> dict[str, Any]:
    return {
        "index": _get(delta, "index") or 0,
        "id": _get(delta, "id"),
        "type": _get(delta, "type") or "function",
        "function": {
            "name": _get(_get(delta, "function"), "name"),
            "arguments": _get(_get(delta, "function"), "arguments") or "",
        },
    }


def merge_tool_call_deltas(
    buffer: dict[int, dict[str, Any]],
    deltas: list[dict[str, Any]],
) -> None:
    for d in deltas:
        idx = d["index"]
        slot = buffer.setdefault(
            idx,
            {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if d.get("id"):
            slot["id"] = d["id"]
        if d.get("type"):
            slot["type"] = d["type"]
        fn = d.get("function", {})
        if fn.get("name"):
            slot["function"]["name"] = fn["name"]
        if fn.get("arguments"):
            slot["function"]["arguments"] += fn["arguments"]


def finalize_tool_calls(buffer: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    import json

    result = []
    for idx in sorted(buffer.keys()):
        slot = buffer[idx]
        args_raw = slot["function"]["arguments"] or "{}"
        try:
            arguments_dict = json.loads(args_raw)
            arguments_str = json.dumps(arguments_dict)
        except json.JSONDecodeError:
            arguments_str = args_raw
        result.append(
            {
                "id": slot["id"] or f"call_{idx}",
                "type": "function",
                "function": {
                    "name": slot["function"]["name"],
                    "arguments": arguments_str,
                },
            }
        )
    return result


def session_entries_to_llm_messages(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in entries:
        role = e.get("role", "user")
        msg: dict[str, Any] = {"role": role, "content": e.get("content", "")}
        if e.get("tool_calls"):
            msg["tool_calls"] = e["tool_calls"]
        if role == "tool" and e.get("tool_call_id"):
            msg["tool_call_id"] = e["tool_call_id"]
        out.append(msg)
    return out
