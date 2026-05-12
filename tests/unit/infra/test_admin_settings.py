"""Tests for admin_user_ids setting (Phase A5)."""

from __future__ import annotations

from pyclaw.infra.settings import Settings


def test_admin_user_ids_default_empty() -> None:
    s = Settings()
    assert s.admin_user_ids == []


def test_admin_user_ids_from_nested_json() -> None:
    data = {"admin": {"userIds": ["ou_abc", "admin_web"]}}
    s = Settings.model_validate(data)
    assert s.admin_user_ids == ["ou_abc", "admin_web"]


def test_admin_user_ids_missing_admin_key() -> None:
    data = {"server": {"port": 9000}}
    s = Settings.model_validate(data)
    assert s.admin_user_ids == []


def test_admin_user_ids_empty_list() -> None:
    data = {"admin": {"userIds": []}}
    s = Settings.model_validate(data)
    assert s.admin_user_ids == []
