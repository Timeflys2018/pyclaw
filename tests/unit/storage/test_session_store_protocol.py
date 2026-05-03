from __future__ import annotations

from pyclaw.storage.protocols import SessionStore as FromProtocols
from pyclaw.storage.session.base import SessionStore as FromBase


def test_all_import_paths_yield_same_protocol() -> None:
    assert FromProtocols is FromBase


def test_protocol_is_typed_not_dict_based() -> None:
    annotations = FromBase.load.__annotations__  # type: ignore[attr-defined]
    assert "session_id" in annotations
    return_type = annotations.get("return")
    assert return_type is not None
    name = getattr(return_type, "__name__", None) or str(return_type)
    assert "dict" not in name.lower() or "SessionTree" in str(return_type)
