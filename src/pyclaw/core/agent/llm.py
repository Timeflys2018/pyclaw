from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pyclaw.infra.settings import ProviderSettings


class LLMErrorCode:
    CONTEXT_OVERFLOW = "context_overflow"
    RATE_LIMIT = "rate_limit"
    AUTH_ERROR = "auth_error"
    TIMEOUT = "timeout"
    PROVIDER_NOT_FOUND = "provider_not_found"
    VISION_NOT_SUPPORTED = "vision_not_support"
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
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


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
    # Wrap-once invariant: an LLMError already carries an explicit code that was
    # set by whoever raised it (e.g. resolve_provider_for_model raising
    # PROVIDER_NOT_FOUND).  Never re-classify it from message text — the
    # keyword heuristic below is for raw upstream exceptions only.
    if isinstance(exc, LLMError):
        return exc.code
    message = str(exc).lower()
    if "context" in message and (
        "overflow" in message or "length" in message or "maximum" in message
    ):
        return LLMErrorCode.CONTEXT_OVERFLOW
    if "rate limit" in message or "too many requests" in message or "429" in message:
        return LLMErrorCode.RATE_LIMIT
    if "auth" in message or "api key" in message or "unauthorized" in message or "401" in message:
        return LLMErrorCode.AUTH_ERROR
    if "timeout" in message or "timed out" in message:
        return LLMErrorCode.TIMEOUT
    return LLMErrorCode.UNKNOWN


def resolve_provider_for_model(
    model: str,
    providers: Mapping[str, ProviderSettings],
    *,
    default_provider: str | None = None,
    unknown_prefix_policy: Literal["fail", "default"] = "fail",
) -> tuple[str, ProviderSettings]:
    for name, ps in providers.items():
        if model in (ps.models or {}):
            return name, ps

    for name, ps in providers.items():
        prefixes = ps.prefixes or [name]
        for prefix in prefixes:
            if model == prefix or model.startswith(prefix + "/"):
                return name, ps

    first_segment = model.split("/", 1)[0]
    if first_segment in providers:
        return first_segment, providers[first_segment]

    if len(providers) == 1:
        only_name, only_ps = next(iter(providers.items()))
        return only_name, only_ps

    if (
        unknown_prefix_policy == "default"
        and default_provider is not None
        and default_provider in providers
    ):
        return default_provider, providers[default_provider]

    raise LLMError(
        LLMErrorCode.PROVIDER_NOT_FOUND,
        f"No provider matches model '{model}'. "
        f"Configured providers: {list(providers)}. "
        f"Declare prefixes[] on a provider, or set agent.unknown_prefix_policy='default' "
        f"and agent.default_provider='<name>' to route unknowns.",
    )


def model_supports_input(
    model: str,
    providers: Mapping[str, ProviderSettings],
    modality: str,
    *,
    default_provider: str | None = None,
    unknown_prefix_policy: Literal["fail", "default"] = "fail",
) -> bool:
    _name, ps = resolve_provider_for_model(
        model,
        providers,
        default_provider=default_provider,
        unknown_prefix_policy=unknown_prefix_policy,
    )
    entry = (ps.models or {}).get(model)
    if entry is None:
        return False
    return modality in entry.modalities.input


def messages_have_user_image_content(messages: list[dict[str, Any]]) -> bool:
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, Mapping):
                continue
            btype = block.get("type")
            if btype == "image_url" or btype == "image":
                return True
    return False


def format_vision_capable_models(
    providers: Mapping[str, ProviderSettings],
) -> str:
    parts: list[str] = []
    for provider_name, ps in providers.items():
        models_dict = ps.models or {}
        vision_ids = [
            mid for mid, entry in models_dict.items() if "image" in entry.modalities.input
        ]
        if vision_ids:
            parts.append(f"📦 {provider_name}: {', '.join(vision_ids)}")
    return "; ".join(parts) if parts else "(none configured)"


_format_vision_capable_models = format_vision_capable_models


class LLMClient:
    def __init__(
        self,
        default_model: str = "gpt-4o-mini",
        api_key: str | None = None,
        api_base: str | None = None,
        *,
        providers: Mapping[str, ProviderSettings] | None = None,
        default_provider: str | None = None,
        unknown_prefix_policy: Literal["fail", "default"] = "fail",
    ) -> None:
        self.default_model = default_model
        if providers:
            self._providers: dict[str, ProviderSettings] = dict(providers)
            self._fallback_key: str | None = None
            self._fallback_base: str | None = None
        else:
            self._providers = {}
            self._fallback_key = api_key
            self._fallback_base = api_base
        self._default_provider = default_provider
        self._unknown_prefix_policy: Literal["fail", "default"] = unknown_prefix_policy

    def _resolve_credentials(self, model: str) -> tuple[str | None, str | None, str | None]:
        if not self._providers:
            return self._fallback_key, self._fallback_base, None
        name, ps = resolve_provider_for_model(
            model,
            self._providers,
            default_provider=self._default_provider,
            unknown_prefix_policy=self._unknown_prefix_policy,
        )
        litellm_provider = ps.litellm_provider if ps.litellm_provider is not None else name
        return ps.api_key, ps.base_url, litellm_provider

    async def stream(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        system: str | list[dict[str, Any]] | None = None,
        idle_seconds: float = 0.0,
        abort_event: asyncio.Event | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        from litellm import acompletion

        from pyclaw.core.agent.runtime_util import (
            AgentAbortedError,
            AgentTimeoutError,
            iterate_with_idle_timeout,
        )

        effective_model = model or self.default_model
        payload_messages = _prepend_system(messages, system)

        api_key, api_base, litellm_provider = self._resolve_credentials(effective_model)

        if self._providers and messages_have_user_image_content(payload_messages):
            if not model_supports_input(
                effective_model,
                self._providers,
                "image",
                default_provider=self._default_provider,
                unknown_prefix_policy=self._unknown_prefix_policy,
            ):
                vision_models_str = format_vision_capable_models(self._providers)
                raise LLMError(
                    LLMErrorCode.VISION_NOT_SUPPORTED,
                    f"Model '{effective_model}' does not have image input capability. "
                    f"Available vision-capable models: {vision_models_str}. "
                    f"Use /model <model_id> to switch.",
                )

        extra: dict[str, Any] = {}
        if api_key:
            extra["api_key"] = api_key
        if api_base:
            extra["api_base"] = api_base
        if litellm_provider:
            extra["custom_llm_provider"] = litellm_provider
        if temperature is not None:
            extra["temperature"] = temperature

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
        system: str | list[dict[str, Any]] | None = None,
        idle_seconds: float = 0.0,
        abort_event: asyncio.Event | None = None,
        temperature: float | None = None,
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
            temperature=temperature,
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


def _prepend_system(
    messages: list[dict[str, Any]], system: str | list[dict[str, Any]] | None
) -> list[dict[str, Any]]:
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

    cache_creation = 0
    cache_read = 0
    details = _get(usage, "prompt_tokens_details")
    if details is not None:
        cache_read = int(_get(details, "cached_tokens") or 0)
        cache_creation = int(_get(details, "cache_creation_tokens") or 0)
    if not cache_read:
        cache_read = int(_get(usage, "cache_read_input_tokens") or 0)
    if not cache_creation:
        cache_creation = int(_get(usage, "cache_creation_input_tokens") or 0)

    return LLMUsage(
        input_tokens=int(prompt),
        output_tokens=int(completion),
        total_tokens=int(total),
        cache_creation_input_tokens=cache_creation,
        cache_read_input_tokens=cache_read,
    )


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
