"""Tests for CuratorSettings configuration."""
import pytest
from pyclaw.infra.settings import CuratorSettings, EvolutionSettings


class TestCuratorSettingsDefaults:
    def test_default_enabled(self):
        s = CuratorSettings()
        assert s.enabled is True

    def test_default_check_interval(self):
        s = CuratorSettings()
        assert s.check_interval_seconds == 3600

    def test_default_interval(self):
        s = CuratorSettings()
        assert s.interval_seconds == 604800

    def test_default_stale_after_days(self):
        s = CuratorSettings()
        assert s.stale_after_days == 30

    def test_default_archive_after_days(self):
        s = CuratorSettings()
        assert s.archive_after_days == 90


class TestCuratorSettingsAlias:
    def test_camel_case_alias_parsing(self):
        s = CuratorSettings.model_validate({
            "checkIntervalSeconds": 7200,
            "intervalSeconds": 1209600,
            "staleAfterDays": 14,
            "archiveAfterDays": 60,
        })
        assert s.check_interval_seconds == 7200
        assert s.interval_seconds == 1209600
        assert s.stale_after_days == 14
        assert s.archive_after_days == 60

    def test_snake_case_also_works(self):
        s = CuratorSettings.model_validate({
            "check_interval_seconds": 1800,
            "interval_seconds": 86400,
        })
        assert s.check_interval_seconds == 1800
        assert s.interval_seconds == 86400


class TestCuratorSettingsEnvVar:
    def test_env_prefix(self, monkeypatch):
        monkeypatch.setenv("PYCLAW_CURATOR_ENABLED", "false")
        monkeypatch.setenv("PYCLAW_CURATOR_ARCHIVE_AFTER_DAYS", "45")
        s = CuratorSettings()
        assert s.enabled is False
        assert s.archive_after_days == 45


class TestEvolutionSettingsNestedCurator:
    def test_curator_nested_default(self):
        e = EvolutionSettings()
        assert e.curator.enabled is True
        assert e.curator.archive_after_days == 90

    def test_curator_nested_from_dict(self):
        e = EvolutionSettings.model_validate({
            "curator": {
                "enabled": False,
                "archiveAfterDays": 30,
            }
        })
        assert e.curator.enabled is False
        assert e.curator.archive_after_days == 30

    def test_extra_fields_ignored(self):
        s = CuratorSettings.model_validate({
            "enabled": True,
            "unknownField": "ignored",
        })
        assert s.enabled is True


class TestCuratorGraduationSettings:
    def test_graduation_defaults(self):
        s = CuratorSettings()
        assert s.graduation_enabled is True
        assert s.graduation_mode == "template"
        assert s.graduation_model is None
        assert s.promotion_min_use_count == 5
        assert s.promotion_min_days == 7

    def test_graduation_alias_parsing(self):
        s = CuratorSettings.model_validate({
            "graduationEnabled": False,
            "graduationMode": "enrich",
            "promotionMinUseCount": 10,
            "promotionMinDays": 14,
        })
        assert s.graduation_enabled is False
        assert s.graduation_mode == "enrich"
        assert s.promotion_min_use_count == 10
        assert s.promotion_min_days == 14


class TestCuratorLLMReviewSettings:
    def test_llm_review_defaults(self):
        s = CuratorSettings()
        assert s.llm_review_enabled is False
        assert s.llm_review_model is None
        assert s.llm_review_interval_seconds == 1209600
        assert s.llm_review_actions == ["promote"]
        assert s.llm_review_max_batch == 20

    def test_llm_review_alias_parsing(self):
        s = CuratorSettings.model_validate({
            "llmReviewEnabled": True,
            "llmReviewActions": ["promote", "archive"],
            "llmReviewMaxBatch": 10,
        })
        assert s.llm_review_enabled is True
        assert s.llm_review_actions == ["promote", "archive"]
        assert s.llm_review_max_batch == 10
