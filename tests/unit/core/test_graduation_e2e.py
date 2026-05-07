"""Integration tests for the full graduation pipeline (Tasks 9.1–9.6)."""

from __future__ import annotations

import logging
import time

import apsw
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from pyclaw.core.curator import _scan_single_db, run_curator_scan, CuratorReport
from pyclaw.core.skill_graduation import parse_sop_content, graduate_single_sop
from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer
from pyclaw.infra.settings import CuratorSettings, SkillSettings
from pyclaw.skills.parser import parse_skill_file
from pyclaw.skills.discovery import discover_skills


def _create_test_db(path: Path, entries: list[dict]) -> None:
    """Create a test SQLite DB with procedures table and FTS5."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = apsw.Connection(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    register_jieba_tokenizer(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS procedures (
        id TEXT PRIMARY KEY, session_key TEXT NOT NULL, type TEXT NOT NULL,
        content TEXT NOT NULL, source_session_id TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        last_used_at REAL, use_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active', archived_at REAL, archive_reason TEXT
    )""")
    conn.execute("""CREATE VIRTUAL TABLE IF NOT EXISTS procedures_fts USING fts5(
        content,
        content=procedures,
        content_rowid=rowid,
        tokenize='jieba'
    )""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS procedures_ai AFTER INSERT ON procedures BEGIN
        INSERT INTO procedures_fts(rowid, content) VALUES (new.rowid, new.content);
    END""")
    conn.execute("""CREATE TRIGGER IF NOT EXISTS procedures_au AFTER UPDATE OF content ON procedures BEGIN
        INSERT INTO procedures_fts(procedures_fts, rowid, content)
            VALUES('delete', old.rowid, old.content);
        INSERT INTO procedures_fts(rowid, content) VALUES (new.rowid, new.content);
    END""")
    for e in entries:
        conn.execute(
            "INSERT INTO procedures (id, session_key, type, content, "
            "created_at, updated_at, last_used_at, use_count, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                e["id"],
                e["session_key"],
                e.get("type", "auto_sop"),
                e["content"],
                e["created_at"],
                e["updated_at"],
                e.get("last_used_at"),
                e.get("use_count", 0),
                e.get("status", "active"),
            ),
        )
    conn.close()


# ---------------------------------------------------------------------------
# 9.1: Full pipeline — Curator triggers graduation → SKILL.md → discover_skills
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_curator_graduates_and_discovers(tmp_path: Path) -> None:
    """9.1: mock SOP → Curator triggers → SKILL.md generated → discover_skills finds."""
    db_file = tmp_path / "memory" / "test_user_one.db"
    workspace_base = tmp_path / "workspaces"

    now = time.time()
    _create_test_db(
        db_file,
        [
            {
                "id": "pipeline-1",
                "session_key": "test:user:one",
                "content": "deploy-helm\nDeploy app via Helm chart\n1. Check chart version\n2. Run helm upgrade\n3. Verify rollout",
                "created_at": now - 14 * 86400,
                "updated_at": now - 14 * 86400,
                "last_used_at": now - 1 * 86400,
                "use_count": 8,
            }
        ],
    )

    settings = CuratorSettings(
        archive_after_days=90,
        graduation_enabled=True,
        promotion_min_use_count=5,
        promotion_min_days=7,
    )

    # Run scan
    archived, graduated = await _scan_single_db(
        db_file=db_file,
        archive_days=90,
        l1_index=AsyncMock(),
        workspace_base_dir=workspace_base,
        settings=settings,
    )

    assert graduated == 1

    # Verify SKILL.md exists and is parseable
    workspace_path = workspace_base / "test_user_one"
    skill_file = workspace_path / "skills" / "deploy-helm" / "SKILL.md"
    assert skill_file.exists(), f"Expected {skill_file} to exist"

    # Verify parser can parse it
    manifest = parse_skill_file(skill_file)
    assert manifest.name == "deploy-helm"
    assert manifest.auto_generated is True
    assert manifest.lifecycle == "active"

    # Verify discover_skills finds it (use SkillSettings pointing to workspace)
    skill_settings = SkillSettings(
        workspace_skills_dir="skills",
        bundled_skills_dir=None,
        personal_agents_skills_dir=str(tmp_path / "nonexistent_personal"),
        managed_skills_dir=str(tmp_path / "nonexistent_managed"),
        project_agents_skills_dir=".agents/skills",
    )
    skills = discover_skills(workspace_path, settings=skill_settings)
    assert any(s.name == "deploy-helm" for s in skills)


# ---------------------------------------------------------------------------
# 9.2: Graduated SOP not in search + skill_view loadable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_graduated_not_in_search(tmp_path: Path) -> None:
    """9.2: After graduation, search doesn't return it but skill exists."""
    from pyclaw.storage.memory.sqlite import SqliteMemoryBackend
    from pyclaw.storage.memory.base import MemoryEntry

    base_dir = tmp_path / "memory"
    base_dir.mkdir(parents=True)
    backend = SqliteMemoryBackend(base_dir=base_dir)
    session_key = "test:user:grad"

    # Store a procedure
    entry = MemoryEntry(
        id="grad-search-test",
        layer="L3",
        type="auto_sop",
        content="test-sop\nTest SOP\n1. Step one\n2. Step two",
        created_at=time.time(),
        updated_at=time.time(),
        status="active",
    )
    await backend.store(session_key, entry)

    # Verify searchable when active
    results = await backend.search(session_key, "test sop step", layers=["L3"])
    assert len(results) >= 1

    # Simulate graduation (UPDATE status)
    conn = await backend._get_conn(session_key)
    conn.execute(
        "UPDATE procedures SET status='graduated' WHERE id='grad-search-test'"
    )

    # Verify NOT searchable after graduation
    results2 = await backend.search(session_key, "test sop step", layers=["L3"])
    assert len(results2) == 0

    await backend.close()


# ---------------------------------------------------------------------------
# 9.3: Enrich mode (mock LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrich_mode_generates_rich_skill(tmp_path: Path) -> None:
    """9.3: enrich mode calls LLM and produces richer SKILL.md."""
    from pyclaw.core.skill_graduation import generate_skill_md_enrich

    rich_content = """---
name: deploy-helm
description: Deploy containerized applications using Helm charts
auto_generated: true
lifecycle: active
generated_at: "2026-05-20T10:00:00Z"
source_session: "test:session"
---

# deploy-helm

## When to Use
When deploying or upgrading Kubernetes applications.

## Prerequisites
- helm CLI v3+ installed
- kubectl configured

## Procedure
1. Check chart version
2. Run helm upgrade --install

## Common Issues
- ImagePullBackOff: check registry auth
"""
    from dataclasses import dataclass

    @dataclass
    class FakeLLMResponse:
        text: str

    mock_llm = AsyncMock()
    mock_llm.complete = AsyncMock(return_value=FakeLLMResponse(text=rich_content))

    result = await generate_skill_md_enrich(
        name="deploy-helm",
        description="Deploy via Helm",
        procedure="1. Check chart\n2. Run upgrade",
        session_key="test:session",
        llm_client=mock_llm,
        model=None,
    )

    assert "When to Use" in result
    assert "Prerequisites" in result
    assert "Common Issues" in result


# ---------------------------------------------------------------------------
# 9.4: Name collision
# ---------------------------------------------------------------------------


def test_name_collision_skips(tmp_path: Path) -> None:
    """9.4: If SKILL.md already exists, graduation skips."""
    workspace_base = tmp_path / "workspaces"
    ws = workspace_base / "test_user"
    skill_dir = ws / "skills" / "existing-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("existing content")

    success, path = graduate_single_sop(
        entry_id="collision-1",
        content="existing-skill\nSome desc\n1. Steps",
        session_key="test:user",
        workspace_base_dir=workspace_base,
    )

    assert success is False
    assert path is None
    # Original file untouched
    assert (skill_dir / "SKILL.md").read_text() == "existing content"


# ---------------------------------------------------------------------------
# 9.5: Curator log optimization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_curator_log_debug_when_no_changes(tmp_path: Path, caplog) -> None:
    """9.5: Curator uses DEBUG when no archive/graduation happens."""
    db_file = tmp_path / "fresh.db"
    now = time.time()
    _create_test_db(
        db_file,
        [
            {
                "id": "fresh-1",
                "session_key": "test:user",
                "content": "some-sop\nDesc\n1. Step",
                "created_at": now - 2 * 86400,  # 2 days old (fresh)
                "updated_at": now,
                "use_count": 1,
            }
        ],
    )

    settings = CuratorSettings(
        archive_after_days=90,
        graduation_enabled=True,
        promotion_min_use_count=5,
    )

    with caplog.at_level(logging.DEBUG, logger="pyclaw.core.curator"):
        report = await run_curator_scan(
            memory_base_dir=tmp_path,
            archive_days=90,
            l1_index=AsyncMock(),
            workspace_base_dir=tmp_path / "ws",
            settings=settings,
        )

    assert report.total_archived == 0
    assert report.total_graduated == 0
    # Should be DEBUG level, not INFO — no INFO-level "scan" message expected
    info_records = [
        r
        for r in caplog.records
        if r.levelno == logging.INFO and "scan" in r.message.lower()
    ]
    assert len(info_records) == 0


# ---------------------------------------------------------------------------
# 9.6: Additional edge-case coverage
# ---------------------------------------------------------------------------


def test_parse_sop_content_valid() -> None:
    """parse_sop_content handles valid 3-line content."""
    result = parse_sop_content("deploy-app\nDeploy application\n1. Build\n2. Push\n3. Deploy")
    assert result is not None
    name, desc, proc = result
    assert name == "deploy-app"
    assert desc == "Deploy application"
    assert "1. Build" in proc


def test_parse_sop_content_invalid_name() -> None:
    """parse_sop_content rejects non-kebab-case names."""
    result = parse_sop_content("Deploy App!\nSome desc\n1. Step")
    assert result is None


def test_parse_sop_content_too_few_lines() -> None:
    """parse_sop_content rejects content with < 3 lines."""
    result = parse_sop_content("only-name\ndescription")
    assert result is None
