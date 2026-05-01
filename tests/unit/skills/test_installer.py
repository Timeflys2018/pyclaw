from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyclaw.skills.clawhub_client import SkillDetail
from pyclaw.skills.installer import (
    _extract_zip,
    _resolve_root,
    _validate_zip_entry,
    _verify_integrity,
    install_skill,
    update_lock_json,
    write_origin_json,
)
from pyclaw.skills.models import SkillInstallError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_zip(files: dict[str, str]) -> bytes:
    """Create an in-memory ZIP archive from a dict of {name: content}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# 1. zip-slip rejection
# ---------------------------------------------------------------------------

class TestValidateZipEntry:
    def test_rejects_path_traversal(self, tmp_path: Path) -> None:
        assert _validate_zip_entry("../escape.txt", tmp_path) is False

    def test_rejects_nested_traversal(self, tmp_path: Path) -> None:
        assert _validate_zip_entry("foo/../../escape.txt", tmp_path) is False

    def test_accepts_normal_entry(self, tmp_path: Path) -> None:
        assert _validate_zip_entry("skill/SKILL.md", tmp_path) is True


class TestExtractZip:
    def test_zip_slip_raises(self, tmp_path: Path) -> None:
        """ZIP with ../escape.txt entry → SkillInstallError."""
        zip_bytes = make_zip({"../escape.txt": "evil"})
        with pytest.raises(SkillInstallError, match="[Zz]ip.slip|escape|unsafe"):
            _extract_zip(zip_bytes, tmp_path)

    # -------------------------------------------------------------------
    # 2. oversized archive
    # -------------------------------------------------------------------

    def test_oversized_archive_raises(self, tmp_path: Path) -> None:
        """Archive exceeding max_archive_bytes → error."""
        zip_bytes = make_zip({"a.txt": "x" * 100})
        with pytest.raises(SkillInstallError, match="[Aa]rchive.*size|too large"):
            _extract_zip(zip_bytes, tmp_path, max_archive_bytes=10)

    # -------------------------------------------------------------------
    # 3. too many entries
    # -------------------------------------------------------------------

    def test_too_many_entries_raises(self, tmp_path: Path) -> None:
        """ZIP with > max_entries files → error."""
        files = {f"file_{i}.txt": "x" for i in range(20)}
        zip_bytes = make_zip(files)
        with pytest.raises(SkillInstallError, match="[Ee]ntr"):
            _extract_zip(zip_bytes, tmp_path, max_entries=5)

    def test_normal_extraction(self, tmp_path: Path) -> None:
        """Normal ZIP extracts correctly."""
        zip_bytes = make_zip({"SKILL.md": "# Hello", "lib/util.py": "pass"})
        _extract_zip(zip_bytes, tmp_path)
        assert (tmp_path / "SKILL.md").read_text() == "# Hello"
        assert (tmp_path / "lib" / "util.py").read_text() == "pass"


# ---------------------------------------------------------------------------
# 4-7. root resolution
# ---------------------------------------------------------------------------

class TestResolveRoot:
    def test_skill_md_at_root(self, tmp_path: Path) -> None:
        """SKILL.md directly in extract dir → return extract dir."""
        (tmp_path / "SKILL.md").write_text("# Skill")
        assert _resolve_root(tmp_path) == tmp_path

    def test_package_subdir(self, tmp_path: Path) -> None:
        """package/SKILL.md → return package/ subdir."""
        pkg = tmp_path / "package"
        pkg.mkdir()
        (pkg / "SKILL.md").write_text("# Skill")
        assert _resolve_root(tmp_path) == pkg

    def test_single_nested_dir(self, tmp_path: Path) -> None:
        """Exactly one subdir containing SKILL.md → return that subdir."""
        nested = tmp_path / "my-skill-v1"
        nested.mkdir()
        (nested / "SKILL.md").write_text("# Skill")
        assert _resolve_root(tmp_path) == nested

    def test_no_skill_md_raises(self, tmp_path: Path) -> None:
        """No SKILL.md anywhere → raises error."""
        (tmp_path / "README.md").write_text("no skill here")
        with pytest.raises(SkillInstallError, match="No SKILL.md found"):
            _resolve_root(tmp_path)


# ---------------------------------------------------------------------------
# 8-10. integrity verification
# ---------------------------------------------------------------------------

class TestVerifyIntegrity:
    def test_hash_matches(self) -> None:
        """Correct hash → no error."""
        data = b"some zip bytes"
        expected = hashlib.sha256(data).hexdigest()
        _verify_integrity(data, expected)

    def test_hash_mismatch(self) -> None:
        """Wrong hash → raises error."""
        data = b"some zip bytes"
        with pytest.raises(SkillInstallError, match="[Hh]ash.*mismatch|integrity"):
            _verify_integrity(data, "0000dead0000beef")

    def test_no_hash_skips(self) -> None:
        """None hash → no verification, no error."""
        _verify_integrity(b"anything", None)


# ---------------------------------------------------------------------------
# 11. write_origin_json
# ---------------------------------------------------------------------------

class TestWriteOriginJson:
    def test_writes_correct_schema(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "my-skill"
        skill_dir.mkdir()

        write_origin_json(skill_dir, slug="acme/search", version="1.2.0")

        origin_path = skill_dir / ".clawhub" / "origin.json"
        assert origin_path.exists()
        data = json.loads(origin_path.read_text())

        assert data["version"] == 1
        assert data["registry"] == "https://clawhub.ai"
        assert data["slug"] == "acme/search"
        assert data["installedVersion"] == "1.2.0"
        assert isinstance(data["installedAt"], int)
        assert data["installedAt"] > 0


# ---------------------------------------------------------------------------
# 12-14. update_lock_json
# ---------------------------------------------------------------------------

class TestUpdateLockJson:
    def test_creates_new_lock_file(self, tmp_path: Path) -> None:
        """No existing lock.json → creates from scratch."""
        update_lock_json(tmp_path, slug="acme/search", version="1.0.0")

        lock_path = tmp_path / ".clawhub" / "lock.json"
        assert lock_path.exists()
        data = json.loads(lock_path.read_text())
        assert data["version"] == 1
        assert "acme/search" in data["skills"]
        assert data["skills"]["acme/search"]["version"] == "1.0.0"
        assert isinstance(data["skills"]["acme/search"]["installedAt"], int)

    def test_merges_with_existing(self, tmp_path: Path) -> None:
        """Existing lock.json with other skills → preserves them, adds new."""
        clawhub_dir = tmp_path / ".clawhub"
        clawhub_dir.mkdir()
        existing = {
            "version": 1,
            "skills": {
                "acme/old-skill": {"version": "2.0.0", "installedAt": 1000},
            },
        }
        (clawhub_dir / "lock.json").write_text(json.dumps(existing))

        update_lock_json(tmp_path, slug="acme/new-skill", version="3.0.0")

        data = json.loads((clawhub_dir / "lock.json").read_text())
        assert "acme/old-skill" in data["skills"]
        assert data["skills"]["acme/old-skill"]["version"] == "2.0.0"
        assert "acme/new-skill" in data["skills"]
        assert data["skills"]["acme/new-skill"]["version"] == "3.0.0"

    def test_overwrites_same_slug(self, tmp_path: Path) -> None:
        """Same slug → updated version and timestamp."""
        update_lock_json(tmp_path, slug="acme/search", version="1.0.0")
        update_lock_json(tmp_path, slug="acme/search", version="2.0.0")

        lock_path = tmp_path / ".clawhub" / "lock.json"
        data = json.loads(lock_path.read_text())
        assert data["skills"]["acme/search"]["version"] == "2.0.0"


# ---------------------------------------------------------------------------
# 15. install_skill end-to-end
# ---------------------------------------------------------------------------

class TestInstallSkill:
    @pytest.mark.asyncio
    async def test_full_flow(self, tmp_path: Path) -> None:
        """Mock client, verify full install flow."""
        skill_content = "# My Skill\nDescription here"
        zip_bytes = make_zip({"package/SKILL.md": skill_content, "package/lib.py": "pass"})
        sha = hashlib.sha256(zip_bytes).hexdigest()

        detail = SkillDetail(
            slug="acme/search",
            name="Search",
            description="A search skill",
            latest_version="1.0.0",
            sha256hash=sha,
        )

        client = AsyncMock()
        client.get_detail.return_value = detail
        client.download.return_value = zip_bytes

        install_dir = tmp_path / "skills"
        install_dir.mkdir()

        result = await install_skill(
            client=client,
            slug="acme/search",
            version=None,
            install_dir=install_dir,
        )

        client.get_detail.assert_called_once_with("acme/search")
        client.download.assert_called_once_with("acme/search", "1.0.0")

        assert result.exists()
        assert (result / "SKILL.md").read_text() == skill_content
        assert (result / "lib.py").read_text() == "pass"

        origin_path = result / ".clawhub" / "origin.json"
        assert origin_path.exists()
        origin = json.loads(origin_path.read_text())
        assert origin["slug"] == "acme/search"
        assert origin["installedVersion"] == "1.0.0"

        lock_path = tmp_path / ".clawhub" / "lock.json"
        assert lock_path.exists()
        lock = json.loads(lock_path.read_text())
        assert "acme/search" in lock["skills"]

    @pytest.mark.asyncio
    async def test_explicit_version_skips_get_detail_for_version(self, tmp_path: Path) -> None:
        """When version is provided, still fetch detail for hash verification."""
        skill_content = "# Skill v2"
        zip_bytes = make_zip({"SKILL.md": skill_content})
        sha = hashlib.sha256(zip_bytes).hexdigest()

        detail = SkillDetail(
            slug="acme/tool",
            name="Tool",
            description="A tool",
            latest_version="2.0.0",
            sha256hash=sha,
        )

        client = AsyncMock()
        client.get_detail.return_value = detail
        client.download.return_value = zip_bytes

        install_dir = tmp_path / "skills"
        install_dir.mkdir()

        result = await install_skill(
            client=client,
            slug="acme/tool",
            version="2.0.0",
            install_dir=install_dir,
        )

        client.download.assert_called_once_with("acme/tool", "2.0.0")
        assert (result / "SKILL.md").read_text() == skill_content
