"""Integration test: Curator detects and graduates eligible SOPs."""
import time

import apsw
import pytest
from pathlib import Path
from unittest.mock import AsyncMock

from pyclaw.core.curator import _scan_single_db
from pyclaw.storage.memory.jieba_tokenizer import register_jieba_tokenizer
from pyclaw.infra.settings import CuratorSettings


def _create_test_db(path, entries):
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
    for e in entries:
        conn.execute(
            "INSERT INTO procedures (id, session_key, type, content, "
            "created_at, updated_at, last_used_at, use_count, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (e["id"], e["session_key"], e.get("type", "auto_sop"),
             e["content"], e["created_at"], e["updated_at"],
             e.get("last_used_at"), e.get("use_count", 0),
             e.get("status", "active")),
        )
    conn.close()


@pytest.mark.asyncio
async def test_curator_graduates_eligible_sop(tmp_path):
    """Eligible SOP (use_count>=5, age>=7d) gets graduated."""
    db_file = tmp_path / "test_user.db"
    workspace_base = tmp_path / "workspaces"

    now = time.time()
    _create_test_db(db_file, [{
        "id": "grad-candidate-1",
        "session_key": "test:user:one",
        "type": "auto_sop",
        "content": "deploy-helm\nDeploy via Helm chart\n1. Check version\n2. Run upgrade",
        "created_at": now - 10 * 86400,
        "updated_at": now - 10 * 86400,
        "last_used_at": now - 2 * 86400,
        "use_count": 7,
    }])

    settings = CuratorSettings(
        archive_after_days=90,
        graduation_enabled=True,
        promotion_min_use_count=5,
        promotion_min_days=7,
    )
    l1_mock = AsyncMock()

    result = await _scan_single_db(
        db_file=db_file,
        archive_days=90,
        l1_index=l1_mock,
        workspace_base_dir=workspace_base,
        settings=settings,
    )

    assert result == (0, 1)

    conn = apsw.Connection(str(db_file))
    row = list(conn.execute("SELECT status FROM procedures WHERE id='grad-candidate-1'"))
    assert row[0][0] == "graduated"
    conn.close()

    skill_file = workspace_base / "test_user_one" / "skills" / "deploy-helm" / "SKILL.md"
    assert skill_file.exists()
    content = skill_file.read_text()
    assert "deploy-helm" in content
    assert "auto_generated: true" in content

    l1_mock.index_remove.assert_called_once_with("test:user:one", "grad-candidate-1")


@pytest.mark.asyncio
async def test_curator_skips_ineligible_sop(tmp_path):
    """SOP with low use_count is NOT graduated."""
    db_file = tmp_path / "test_user.db"
    workspace_base = tmp_path / "workspaces"

    now = time.time()
    _create_test_db(db_file, [{
        "id": "low-use-1",
        "session_key": "test:user:two",
        "type": "auto_sop",
        "content": "some-sop\nDescription\n1. Step",
        "created_at": now - 10 * 86400,
        "updated_at": now - 10 * 86400,
        "use_count": 2,
    }])

    settings = CuratorSettings(
        archive_after_days=90,
        graduation_enabled=True,
        promotion_min_use_count=5,
        promotion_min_days=7,
    )

    result = await _scan_single_db(
        db_file=db_file,
        archive_days=90,
        l1_index=AsyncMock(),
        workspace_base_dir=workspace_base,
        settings=settings,
    )

    assert result == (0, 0)

    conn = apsw.Connection(str(db_file))
    row = list(conn.execute("SELECT status FROM procedures WHERE id='low-use-1'"))
    assert row[0][0] == "active"
    conn.close()


@pytest.mark.asyncio
async def test_curator_skips_graduation_when_disabled(tmp_path):
    """Graduation is skipped when graduation_enabled=False."""
    db_file = tmp_path / "test_user.db"
    workspace_base = tmp_path / "workspaces"

    now = time.time()
    _create_test_db(db_file, [{
        "id": "eligible-but-disabled",
        "session_key": "test:user:three",
        "type": "auto_sop",
        "content": "deploy-k8s\nDeploy to K8s\n1. Apply manifests",
        "created_at": now - 10 * 86400,
        "updated_at": now - 10 * 86400,
        "last_used_at": now - 1 * 86400,
        "use_count": 10,
    }])

    settings = CuratorSettings(
        archive_after_days=90,
        graduation_enabled=False,
        promotion_min_use_count=5,
        promotion_min_days=7,
    )

    result = await _scan_single_db(
        db_file=db_file,
        archive_days=90,
        l1_index=AsyncMock(),
        workspace_base_dir=workspace_base,
        settings=settings,
    )

    assert result == (0, 0)

    conn = apsw.Connection(str(db_file))
    row = list(conn.execute("SELECT status FROM procedures WHERE id='eligible-but-disabled'"))
    assert row[0][0] == "active"
    conn.close()
