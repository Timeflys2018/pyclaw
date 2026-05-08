from __future__ import annotations

import pytest

from pyclaw.core.commands._helpers import parse_idle_duration


def test_parse_idle_duration_minutes() -> None:
    assert parse_idle_duration("30m") == 30
    assert parse_idle_duration("5min") == 5
    assert parse_idle_duration("60mins") == 60
    assert parse_idle_duration("45minutes") == 45


def test_parse_idle_duration_hours() -> None:
    assert parse_idle_duration("2h") == 120
    assert parse_idle_duration("1hour") == 60
    assert parse_idle_duration("3hours") == 180


def test_parse_idle_duration_off() -> None:
    assert parse_idle_duration("off") == 0
    assert parse_idle_duration("0") == 0
    assert parse_idle_duration("disable") == 0
    assert parse_idle_duration("关闭") == 0


def test_parse_idle_duration_invalid() -> None:
    assert parse_idle_duration("garbage") is None
    assert parse_idle_duration("") is None
    assert parse_idle_duration("abc") is None
    assert parse_idle_duration("1d") is None
