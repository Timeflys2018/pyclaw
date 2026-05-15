from __future__ import annotations

import hashlib
import io
import json
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any

from pyclaw.skills.clawhub_client import ClawHubClient
from pyclaw.skills.models import SkillInstallError


def _validate_zip_entry(entry_name: str, target_dir: Path) -> bool:
    if ".." in Path(entry_name).parts:
        return False
    resolved = (target_dir / entry_name).resolve()
    return resolved.is_relative_to(target_dir.resolve())


def _extract_zip(
    zip_bytes: bytes,
    target_dir: Path,
    max_archive_bytes: int = 256_000_000,
    max_extracted_bytes: int = 512_000_000,
    max_entries: int = 50_000,
) -> None:
    if len(zip_bytes) > max_archive_bytes:
        raise SkillInstallError(f"Archive size {len(zip_bytes)} exceeds limit {max_archive_bytes}")

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = zf.namelist()
        if len(names) > max_entries:
            raise SkillInstallError(f"Too many entries ({len(names)}), limit is {max_entries}")

        total_extracted = 0
        for entry in names:
            if not _validate_zip_entry(entry, target_dir):
                raise SkillInstallError(f"Zip-slip detected for entry: {entry}")

            info = zf.getinfo(entry)
            total_extracted += info.file_size
            if total_extracted > max_extracted_bytes:
                raise SkillInstallError(f"Extracted size exceeds limit {max_extracted_bytes}")

            dest = target_dir / entry
            if entry.endswith("/"):
                dest.mkdir(parents=True, exist_ok=True)
            else:
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(entry))


def _resolve_root(extract_dir: Path) -> Path:
    if (extract_dir / "package" / "SKILL.md").exists():
        return extract_dir / "package"

    if (extract_dir / "SKILL.md").exists():
        return extract_dir

    subdirs = [d for d in extract_dir.iterdir() if d.is_dir()]
    candidates = [d for d in subdirs if (d / "SKILL.md").exists()]
    if len(candidates) == 1:
        return candidates[0]

    raise SkillInstallError("No SKILL.md found in archive")


def _verify_integrity(zip_bytes: bytes, expected_hash: str | None) -> None:
    if expected_hash is None:
        return
    actual = hashlib.sha256(zip_bytes).hexdigest()
    if actual.lower() != expected_hash.lower():
        raise SkillInstallError(f"Hash mismatch: expected {expected_hash}, got {actual}")


def write_origin_json(
    skill_dir: Path,
    slug: str,
    version: str,
    registry: str = "https://clawhub.ai",
) -> None:
    clawhub_dir = skill_dir / ".clawhub"
    clawhub_dir.mkdir(parents=True, exist_ok=True)
    origin = {
        "version": 1,
        "registry": registry,
        "slug": slug,
        "installedVersion": version,
        "installedAt": int(time.time() * 1000),
    }
    (clawhub_dir / "origin.json").write_text(json.dumps(origin, indent=2), encoding="utf-8")


def update_lock_json(workspace_dir: Path, slug: str, version: str) -> None:
    clawhub_dir = workspace_dir / ".clawhub"
    clawhub_dir.mkdir(parents=True, exist_ok=True)
    lock_path = clawhub_dir / "lock.json"

    data: dict[str, Any]
    if lock_path.exists():
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    else:
        data = {"version": 1, "skills": {}}

    data["skills"][slug] = {
        "version": version,
        "installedAt": int(time.time() * 1000),
    }
    lock_path.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def install_skill(
    client: ClawHubClient,
    slug: str,
    version: str | None,
    install_dir: Path,
) -> Path:
    detail = await client.get_detail(slug)
    if version is None:
        version = detail.latest_version

    zip_bytes = await client.download(slug, version)
    _verify_integrity(zip_bytes, detail.sha256hash)

    tmp_dir = Path(tempfile.mkdtemp())
    try:
        _extract_zip(zip_bytes, tmp_dir)
        root = _resolve_root(tmp_dir)

        dest = install_dir / slug
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(root, dest)

        write_origin_json(dest, slug, version)
        update_lock_json(install_dir.parent, slug, version)

        return dest
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
