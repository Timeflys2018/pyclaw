from __future__ import annotations

import argparse
import time
import uuid
from pathlib import Path
from unittest.mock import patch

import apsw
import pytest

from pyclaw.cli.skills import cmd_curator_graduate, cmd_curator_list, cmd_curator_restore


_SCHEMA = """
CREATE TABLE procedures (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL,
    type TEXT NOT NULL,
    content TEXT NOT NULL,
    source_session_id TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    last_used_at REAL,
    use_count INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    archived_at REAL,
    archive_reason TEXT
)
"""


def _create_test_db(tmp_path: Path, name: str = "test_user.db") -> Path:
    db_path = tmp_path / name
    conn = apsw.Connection(str(db_path))
    conn.execute(_SCHEMA)
    conn.close()
    return db_path


def _insert_procedure(
    db_path: Path,
    *,
    proc_id: str | None = None,
    type_: str = "auto_sop",
    content: str = "test procedure",
    status: str = "active",
    use_count: int = 1,
    created_at: float | None = None,
    last_used_at: float | None = None,
    archived_at: float | None = None,
    archive_reason: str | None = None,
) -> str:
    proc_id = proc_id or str(uuid.uuid4())
    now = time.time()
    created_at = created_at or now
    conn = apsw.Connection(str(db_path))
    conn.execute(
        "INSERT INTO procedures (id, session_key, type, content, created_at, updated_at, "
        "last_used_at, use_count, status, archived_at, archive_reason) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (proc_id, "user1", type_, content, created_at, now, last_used_at, use_count, status, archived_at, archive_reason),
    )
    conn.close()
    return proc_id


@pytest.fixture()
def memory_dir(tmp_path: Path):
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    return mem_dir


@pytest.fixture()
def mock_settings(memory_dir: Path):
    with patch("pyclaw.cli.skills.load_settings") as mock_ls, \
         patch("pyclaw.cli.skills._get_memory_dbs") as mock_dbs:

        class FakeMemory:
            base_dir = str(memory_dir)

        class FakeCurator:
            stale_after_days = 30

        class FakeEvolution:
            curator = FakeCurator()

        class FakeSettings:
            memory = FakeMemory()
            evolution = FakeEvolution()

        mock_ls.return_value = FakeSettings()
        yield mock_ls, mock_dbs, memory_dir


class TestCuratorListAuto:
    def test_shows_active_auto_sop_entries(self, memory_dir: Path, capsys):
        db_path = _create_test_db(memory_dir)
        proc_id = _insert_procedure(db_path, type_="auto_sop", content="Deploy to staging", use_count=5, last_used_at=time.time())

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]), \
             patch("pyclaw.infra.settings.load_settings") as mock_ls:

            class FakeCurator:
                stale_after_days = 30

            class FakeEvolution:
                curator = FakeCurator()

            class FakeSettings:
                evolution = FakeEvolution()

            mock_ls.return_value = FakeSettings()

            args = argparse.Namespace(auto=True, stale=False, archived=False)
            cmd_curator_list(args)

        output = capsys.readouterr().out
        assert proc_id[:8] in output
        assert "Deploy to staging" in output

    def test_no_results_when_empty(self, memory_dir: Path, capsys):
        db_path = _create_test_db(memory_dir)

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]), \
             patch("pyclaw.infra.settings.load_settings") as mock_ls:

            class FakeSettings:
                pass

            mock_ls.return_value = FakeSettings()

            args = argparse.Namespace(auto=True, stale=False, archived=False)
            cmd_curator_list(args)

        output = capsys.readouterr().out
        assert "Deploy" not in output


class TestCuratorListStale:
    def test_shows_stale_entries(self, memory_dir: Path, capsys):
        db_path = _create_test_db(memory_dir)
        sixty_days_ago = time.time() - 60 * 86400
        proc_id = _insert_procedure(
            db_path, content="Old stale procedure", created_at=sixty_days_ago, last_used_at=sixty_days_ago,
        )
        _insert_procedure(db_path, content="Fresh procedure", last_used_at=time.time())

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]), \
             patch("pyclaw.infra.settings.load_settings") as mock_ls:

            class FakeCurator:
                stale_after_days = 30

            class FakeEvolution:
                curator = FakeCurator()

            class FakeSettings:
                evolution = FakeEvolution()

            mock_ls.return_value = FakeSettings()

            args = argparse.Namespace(auto=False, stale=True, archived=False)
            cmd_curator_list(args)

        output = capsys.readouterr().out
        assert proc_id[:8] in output
        assert "Old stale" in output
        assert "Fresh procedure" not in output


class TestCuratorListArchived:
    def test_shows_archived_entries(self, memory_dir: Path, capsys):
        db_path = _create_test_db(memory_dir)
        archived_time = time.time() - 10 * 86400
        proc_id = _insert_procedure(
            db_path,
            content="Archived SOP",
            status="archived",
            archived_at=archived_time,
            archive_reason="stale",
        )

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]), \
             patch("pyclaw.infra.settings.load_settings") as mock_ls:

            class FakeSettings:
                pass

            mock_ls.return_value = FakeSettings()

            args = argparse.Namespace(auto=False, stale=False, archived=True)
            cmd_curator_list(args)

        output = capsys.readouterr().out
        assert proc_id[:8] in output
        assert "Archived SOP" in output
        assert "stale" in output


class TestCuratorRestore:
    def test_restore_success(self, memory_dir: Path, capsys):
        db_path = _create_test_db(memory_dir)
        proc_id = _insert_procedure(
            db_path,
            content="Should be restored",
            status="archived",
            archived_at=time.time(),
            archive_reason="stale",
        )

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]):
            args = argparse.Namespace(entry_id=proc_id[:8])
            cmd_curator_restore(args)

        output = capsys.readouterr().out
        assert "Restored" in output

        conn = apsw.Connection(str(db_path))
        row = conn.execute("SELECT status, archived_at, archive_reason FROM procedures WHERE id=?", (proc_id,)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "active"
        assert row[1] is None
        assert row[2] is None

    def test_restore_nonexistent(self, memory_dir: Path, capsys):
        db_path = _create_test_db(memory_dir)

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]):
            args = argparse.Namespace(entry_id="nonexist")
            with pytest.raises(SystemExit) as exc_info:
                cmd_curator_restore(args)
            assert exc_info.value.code == 1

        output = capsys.readouterr().out
        assert "No archived entry found" in output


class TestCuratorGraduate:
    def _make_sop_content(self, name: str = "deploy-staging") -> str:
        return f"{name}\nDeploy to staging environment\nStep 1: run deploy\nStep 2: verify"

    def _make_settings(self, workspace_base: Path):
        class FakeWorkspaces:
            default = str(workspace_base)

        class FakeCurator:
            promotion_min_use_count = 5
            promotion_min_days = 7
            graduation_mode = "template"

        class FakeEvolution:
            curator = FakeCurator()

        class FakeMemory:
            base_dir = "~/.pyclaw/memory"

        class FakeSettings:
            workspaces = FakeWorkspaces()
            evolution = FakeEvolution()
            memory = FakeMemory()

        return FakeSettings()

    def test_graduate_preview(self, memory_dir: Path, tmp_path: Path, capsys):
        db_path = _create_test_db(memory_dir)
        old_ts = time.time() - 30 * 86400
        proc_id = _insert_procedure(
            db_path,
            content=self._make_sop_content(),
            use_count=10,
            created_at=old_ts,
        )

        workspace_base = tmp_path / "workspaces"
        workspace_base.mkdir()
        settings = self._make_settings(workspace_base)

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]), \
             patch("pyclaw.infra.settings.load_settings", return_value=settings):
            args = argparse.Namespace(preview=True, id=None)
            cmd_curator_graduate(args)

        output = capsys.readouterr().out
        assert proc_id[:8] in output
        assert "deploy-staging" in output

    def test_graduate_execute(self, memory_dir: Path, tmp_path: Path, capsys):
        db_path = _create_test_db(memory_dir)
        old_ts = time.time() - 30 * 86400
        proc_id = _insert_procedure(
            db_path,
            content=self._make_sop_content(),
            use_count=10,
            created_at=old_ts,
        )

        workspace_base = tmp_path / "workspaces"
        workspace_base.mkdir()
        settings = self._make_settings(workspace_base)

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]), \
             patch("pyclaw.infra.settings.load_settings", return_value=settings):
            args = argparse.Namespace(preview=False, id=None)
            cmd_curator_graduate(args)

        output = capsys.readouterr().out
        assert "Graduated" in output
        assert "SKILL.md" in output

        conn = apsw.Connection(str(db_path))
        row = conn.execute("SELECT status FROM procedures WHERE id=?", (proc_id,)).fetchone()
        conn.close()
        assert row[0] == "graduated"

        skill_file = workspace_base / "user1" / "skills" / "deploy-staging" / "SKILL.md"
        assert skill_file.exists()

    def test_graduate_by_id(self, memory_dir: Path, tmp_path: Path, capsys):
        db_path = _create_test_db(memory_dir)
        proc_id = _insert_procedure(
            db_path,
            content=self._make_sop_content("force-grad"),
            use_count=1,
            created_at=time.time(),
        )

        workspace_base = tmp_path / "workspaces"
        workspace_base.mkdir()
        settings = self._make_settings(workspace_base)

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]), \
             patch("pyclaw.infra.settings.load_settings", return_value=settings):
            args = argparse.Namespace(preview=False, id=proc_id[:8])
            cmd_curator_graduate(args)

        output = capsys.readouterr().out
        assert "Graduated" in output

        conn = apsw.Connection(str(db_path))
        row = conn.execute("SELECT status FROM procedures WHERE id=?", (proc_id,)).fetchone()
        conn.close()
        assert row[0] == "graduated"

    def test_graduate_no_candidates(self, memory_dir: Path, tmp_path: Path, capsys):
        db_path = _create_test_db(memory_dir)
        _insert_procedure(
            db_path,
            content=self._make_sop_content(),
            use_count=1,
            created_at=time.time(),
        )

        workspace_base = tmp_path / "workspaces"
        workspace_base.mkdir()
        settings = self._make_settings(workspace_base)

        with patch("pyclaw.cli.skills._get_memory_dbs", return_value=[db_path]), \
             patch("pyclaw.infra.settings.load_settings", return_value=settings):
            args = argparse.Namespace(preview=False, id=None)
            cmd_curator_graduate(args)

        output = capsys.readouterr().out
        assert "No graduation candidates found" in output
