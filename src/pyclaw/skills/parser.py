from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from pyclaw.skills.models import (
    InstallSpec,
    SkillManifest,
    SkillParseError,
    SkillRequirements,
)

logger = logging.getLogger(__name__)

_VALID_INSTALL_KINDS = frozenset({"brew", "node", "uv", "go", "download"})


def _extract_frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith("---"):
        raise SkillParseError("No YAML frontmatter found (file must start with ---)")

    end_index = content.find("---", 3)
    if end_index == -1:
        raise SkillParseError("No closing --- for YAML frontmatter")

    frontmatter = content[3:end_index].strip()
    body = content[end_index + 3 :].strip()
    return frontmatter, body


def _parse_install_specs(raw_list: list[Any]) -> list[InstallSpec]:
    specs: list[InstallSpec] = []
    for entry in raw_list:
        if not isinstance(entry, dict):
            logger.warning("Skipping non-dict install entry: %s", entry)
            continue

        kind = entry.get("kind", "")
        if kind not in _VALID_INSTALL_KINDS:
            logger.warning("Skipping install entry with unknown kind: %s", kind)
            continue

        specs.append(
            InstallSpec(
                kind=kind,
                formula=entry.get("formula"),
                package=entry.get("package"),
                module=entry.get("module"),
                url=entry.get("url"),
                bins=entry.get("bins", []),
                os_filter=entry.get("os_filter", []),
                label=entry.get("label"),
            )
        )
    return specs


def _parse_requirements(openclaw: dict[str, Any]) -> SkillRequirements:
    requires: dict[str, Any] = openclaw.get("requires") or {}
    return SkillRequirements(
        bins=requires.get("bins", []),
        any_bins=requires.get("anyBins", []),
        env=requires.get("env", []),
        os=openclaw.get("os", []),
    )


def parse_skill_file(path: str | Path) -> SkillManifest:
    resolved = Path(path).resolve()
    file_path_str = str(resolved)

    try:
        content = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise SkillParseError(str(exc), file_path=file_path_str) from exc

    try:
        frontmatter_str, body = _extract_frontmatter(content)
    except SkillParseError as exc:
        raise SkillParseError(exc.message, file_path=file_path_str) from exc

    try:
        frontmatter: dict[str, Any] = yaml.safe_load(frontmatter_str)
    except yaml.YAMLError as exc:
        raise SkillParseError(f"Invalid YAML: {exc}", file_path=file_path_str) from exc

    if not isinstance(frontmatter, dict):
        raise SkillParseError("Frontmatter is not a YAML mapping", file_path=file_path_str)

    name: str = frontmatter.get("name") or resolved.parent.name
    description: str = frontmatter.get("description", "")

    openclaw: dict[str, Any] = (frontmatter.get("metadata") or {}).get("openclaw") or {}

    requirements = _parse_requirements(openclaw)
    install_specs = _parse_install_specs(openclaw.get("install") or [])
    always = bool(openclaw.get("always", False))
    emoji: str | None = openclaw.get("emoji")
    disable_model_invocation = bool(frontmatter.get("disable-model-invocation", False))

    return SkillManifest(
        name=name,
        description=description,
        body=body,
        file_path=file_path_str,
        requirements=requirements,
        install_specs=install_specs,
        always=always,
        emoji=emoji,
        disable_model_invocation=disable_model_invocation,
        auto_generated=bool(frontmatter.get("auto_generated", False)),
        lifecycle=frontmatter.get("lifecycle", "active"),
        generated_at=frontmatter.get("generated_at"),
        source_session=frontmatter.get("source_session"),
    )
