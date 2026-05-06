from pyclaw.infra.settings import EvolutionSettings, Settings


def test_evolution_settings_defaults():
    s = EvolutionSettings()
    assert s.enabled is True
    assert s.extraction_model is None
    assert s.max_candidates == 100
    assert s.min_tool_calls_for_extraction == 2
    assert s.dedup_overlap_threshold == 0.6
    assert s.max_sops_per_extraction == 5
    assert s.description_max_chars == 150
    assert s.procedure_max_chars == 5000


def test_evolution_settings_env_override(monkeypatch):
    monkeypatch.setenv("PYCLAW_EVOLUTION_ENABLED", "false")
    monkeypatch.setenv("PYCLAW_EVOLUTION_MAX_CANDIDATES", "50")
    monkeypatch.setenv("PYCLAW_EVOLUTION_EXTRACTION_MODEL", "gemini/gemini-2.0-flash")
    s = EvolutionSettings()
    assert s.enabled is False
    assert s.max_candidates == 50
    assert s.extraction_model == "gemini/gemini-2.0-flash"


def test_evolution_settings_in_root_settings():
    settings = Settings()
    assert hasattr(settings, "evolution")
    assert isinstance(settings.evolution, EvolutionSettings)
    assert settings.evolution.enabled is True


def test_evolution_settings_json_parse():
    data = {
        "evolution": {
            "enabled": False,
            "extraction_model": "gpt-4o-mini",
            "max_candidates": 200,
            "minToolCallsForExtraction": 5,
            "dedupOverlapThreshold": 0.8,
            "maxSopsPerExtraction": 3,
            "descriptionMaxChars": 200,
            "procedureMaxChars": 8000,
        }
    }
    settings = Settings.model_validate(data)
    assert settings.evolution.enabled is False
    assert settings.evolution.extraction_model == "gpt-4o-mini"
    assert settings.evolution.max_candidates == 200
    assert settings.evolution.min_tool_calls_for_extraction == 5
    assert settings.evolution.dedup_overlap_threshold == 0.8
    assert settings.evolution.max_sops_per_extraction == 3
    assert settings.evolution.description_max_chars == 200
    assert settings.evolution.procedure_max_chars == 8000
