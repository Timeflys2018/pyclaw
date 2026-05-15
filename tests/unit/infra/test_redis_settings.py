from __future__ import annotations

from pyclaw.infra.settings import RedisSettings


def test_url_built_from_host_port() -> None:
    s = RedisSettings(host="myhost", port=6380)
    assert s.build_url() == "redis://myhost:6380"


def test_url_built_with_password() -> None:
    s = RedisSettings(host="myhost", port=6380, password="secret")
    assert s.build_url() == "redis://:secret@myhost:6380"


def test_explicit_url_takes_precedence() -> None:
    s = RedisSettings(host="myhost", port=6380, url="redis://override:9999")
    assert s.build_url() == "redis://override:9999"


def test_ttl_seconds_from_retention_days() -> None:
    s = RedisSettings(transcript_retention_days=30)
    assert s.ttl_seconds == 30 * 86_400


def test_default_ttl_seven_days() -> None:
    s = RedisSettings()
    assert s.ttl_seconds == 7 * 86_400


def test_json_alias_keyPrefix() -> None:
    s = RedisSettings.model_validate({"keyPrefix": "openclaw:"})
    assert s.key_prefix == "openclaw:"


def test_json_alias_transcriptRetentionDays() -> None:
    s = RedisSettings.model_validate({"transcriptRetentionDays": 30})
    assert s.transcript_retention_days == 30


def test_snake_case_key_prefix_still_works() -> None:
    s = RedisSettings.model_validate({"key_prefix": "test:"})
    assert s.key_prefix == "test:"
