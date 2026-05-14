from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def _to_namespace(obj: Any) -> Any:
    if isinstance(obj, dict):
        return SimpleNamespace(**{k: _to_namespace(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return [_to_namespace(item) for item in obj]
    return obj


def reconstruct_feishu_event(payload: dict[str, Any]) -> Any:
    if not isinstance(payload, dict):
        raise ValueError(f"feishu event payload must be a dict, got {type(payload).__name__}")
    return _to_namespace(payload)
