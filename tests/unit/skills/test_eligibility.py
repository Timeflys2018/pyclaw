from __future__ import annotations

import pytest

from pyclaw.skills.eligibility import (
    check_any_bins,
    check_bins,
    check_env,
    check_os,
    filter_eligible,
    is_eligible,
)
from pyclaw.skills.models import SkillManifest, SkillRequirements

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _skill(
    name: str = "test-skill",
    *,
    os: list[str] | None = None,
    bins: list[str] | None = None,
    any_bins: list[str] | None = None,
    env: list[str] | None = None,
    always: bool = False,
    disable_model_invocation: bool = False,
) -> SkillManifest:
    reqs = SkillRequirements(
        os=os or [],
        bins=bins or [],
        any_bins=any_bins or [],
        env=env or [],
    )
    return SkillManifest(
        name=name,
        requirements=reqs,
        always=always,
        disable_model_invocation=disable_model_invocation,
    )


# ---------------------------------------------------------------------------
# 1. OS rejects even with always=True
# ---------------------------------------------------------------------------


def test_os_rejects_even_with_always(monkeypatch: pytest.MonkeyPatch) -> None:
    """OS mismatch is absolute — `always=True` cannot bypass it."""
    monkeypatch.setattr("sys.platform", "darwin")
    skill = _skill(os=["linux"], always=True)
    assert is_eligible(skill) is False


# ---------------------------------------------------------------------------
# 2. OS passes, always bypasses remaining requires
# ---------------------------------------------------------------------------


def test_os_passes_always_bypasses_requires(monkeypatch: pytest.MonkeyPatch) -> None:
    """OS matches + always=True → skip bins/env checks → eligible."""
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("shutil.which", lambda _b: None)  # all bins "missing"
    skill = _skill(os=["darwin"], always=True, bins=["nonexistent"])
    assert is_eligible(skill) is True


# ---------------------------------------------------------------------------
# 3. No OS field, always bypasses
# ---------------------------------------------------------------------------


def test_no_os_always_bypasses(monkeypatch: pytest.MonkeyPatch) -> None:
    """No OS constraint + always=True → eligible despite missing bins."""
    monkeypatch.setattr("shutil.which", lambda _b: None)
    skill = _skill(always=True, bins=["nonexistent"])
    assert is_eligible(skill) is True


# ---------------------------------------------------------------------------
# 4. bins all present
# ---------------------------------------------------------------------------


def test_bins_all_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """All required bins found on PATH → eligible."""
    monkeypatch.setattr("shutil.which", lambda b: f"/usr/bin/{b}")
    skill = _skill(bins=["python3"])
    assert is_eligible(skill) is True


# ---------------------------------------------------------------------------
# 5. bins one missing
# ---------------------------------------------------------------------------


def test_bins_one_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """One required bin missing → rejected."""

    def _which(b: str) -> str | None:
        return f"/usr/bin/{b}" if b == "python3" else None

    monkeypatch.setattr("shutil.which", _which)
    skill = _skill(bins=["python3", "nonexistent_binary_xyz"])
    assert is_eligible(skill) is False


# ---------------------------------------------------------------------------
# 6. anyBins one present
# ---------------------------------------------------------------------------


def test_any_bins_one_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """At least one of anyBins present → eligible."""

    def _which(b: str) -> str | None:
        return "/usr/bin/python3" if b == "python3" else None

    monkeypatch.setattr("shutil.which", _which)
    skill = _skill(any_bins=["nonexistent1", "python3"])
    assert is_eligible(skill) is True


# ---------------------------------------------------------------------------
# 7. anyBins none present
# ---------------------------------------------------------------------------


def test_any_bins_none_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """None of anyBins present → rejected."""
    monkeypatch.setattr("shutil.which", lambda _b: None)
    skill = _skill(any_bins=["nonexistent1", "nonexistent2"])
    assert is_eligible(skill) is False


# ---------------------------------------------------------------------------
# 8. env present
# ---------------------------------------------------------------------------


def test_env_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """All required env vars set → eligible."""
    monkeypatch.setenv("PYCLAW_TEST_VAR", "1")
    skill = _skill(env=["PYCLAW_TEST_VAR"])
    assert is_eligible(skill) is True


# ---------------------------------------------------------------------------
# 9. env missing
# ---------------------------------------------------------------------------


def test_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Required env var missing → rejected."""
    monkeypatch.delenv("PYCLAW_MISSING_VAR", raising=False)
    skill = _skill(env=["PYCLAW_MISSING_VAR"])
    assert is_eligible(skill) is False


# ---------------------------------------------------------------------------
# 10. No requirements → eligible
# ---------------------------------------------------------------------------


def test_no_requirements() -> None:
    """Skill with no requirements at all → eligible."""
    skill = _skill()
    assert is_eligible(skill) is True


# ---------------------------------------------------------------------------
# 11. disable_model_invocation exclusion
# ---------------------------------------------------------------------------


def test_disable_model_invocation_excluded() -> None:
    """Eligible skill with disable_model_invocation=True excluded from filter."""
    skill = _skill(disable_model_invocation=True)
    assert is_eligible(skill) is True  # eligible on its own
    assert filter_eligible([skill]) == []  # excluded by filter


# ---------------------------------------------------------------------------
# 12. Mixed eligibility
# ---------------------------------------------------------------------------


def test_mixed_eligibility(monkeypatch: pytest.MonkeyPatch) -> None:
    """5 skills, 3 eligible, 2 not → filter returns 3."""
    monkeypatch.setattr("shutil.which", lambda _b: None)

    skills = [
        _skill(name="a"),  # eligible (no reqs)
        _skill(name="b", bins=["missing"]),  # rejected
        _skill(name="c", always=True, bins=["missing"]),  # eligible (always)
        _skill(name="d", any_bins=["missing1", "missing2"]),  # rejected
        _skill(name="e"),  # eligible (no reqs)
    ]
    result = filter_eligible(skills)
    assert [s.name for s in result] == ["a", "c", "e"]


# ---------------------------------------------------------------------------
# 13. filter preserves order
# ---------------------------------------------------------------------------


def test_filter_preserves_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Eligible skills maintain their original input order."""
    monkeypatch.setattr("shutil.which", lambda b: f"/usr/bin/{b}")
    skills = [_skill(name=n) for n in ("z", "a", "m", "b")]
    result = filter_eligible(skills)
    assert [s.name for s in result] == ["z", "a", "m", "b"]


# ---------------------------------------------------------------------------
# Unit tests for individual check_* functions
# ---------------------------------------------------------------------------


class TestCheckOs:
    def test_empty_os_list(self) -> None:
        assert check_os([]) is True

    def test_matching_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "darwin")
        assert check_os(["darwin", "linux"]) is True

    def test_non_matching_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("sys.platform", "win32")
        assert check_os(["darwin", "linux"]) is False


class TestCheckBins:
    def test_empty_bins(self) -> None:
        assert check_bins([]) is True

    def test_all_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda _b: "/usr/bin/x")
        assert check_bins(["a", "b"]) is True

    def test_one_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda b: "/x" if b == "a" else None)
        assert check_bins(["a", "b"]) is False


class TestCheckAnyBins:
    def test_empty_any_bins(self) -> None:
        assert check_any_bins([]) is True

    def test_one_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda b: "/x" if b == "b" else None)
        assert check_any_bins(["a", "b"]) is True

    def test_none_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("shutil.which", lambda _b: None)
        assert check_any_bins(["a", "b"]) is False


class TestCheckEnv:
    def test_empty_env(self) -> None:
        assert check_env([]) is True

    def test_all_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X", "1")
        monkeypatch.setenv("Y", "2")
        assert check_env(["X", "Y"]) is True

    def test_one_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X", "1")
        monkeypatch.delenv("Y", raising=False)
        assert check_env(["X", "Y"]) is False

    def test_empty_value_is_falsy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("X", "")
        assert check_env(["X"]) is False
