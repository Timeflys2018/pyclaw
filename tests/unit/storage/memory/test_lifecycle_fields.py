"""Tests for L3 lifecycle field activation (use_count, last_used_at)."""

import time

import pytest

from pyclaw.storage.memory.base import MemoryEntry
from pyclaw.storage.memory.sqlite import SqliteMemoryBackend


@pytest.fixture
def backend(tmp_path):
    return SqliteMemoryBackend(base_dir=tmp_path)


@pytest.fixture
async def seeded_backend(backend):
    """Backend with one L3 procedure entry."""
    entry = MemoryEntry(
        id="proc_1",
        layer="L3",
        type="workflow",
        content="Deploy application: 1) build docker image 2) push to registry 3) apply k8s manifest",
        source_session_id="ses_test",
        created_at=time.time(),
        updated_at=time.time(),
        status="active",
    )
    await backend.store("test_user", entry)
    return backend


@pytest.mark.asyncio
async def test_search_increments_use_count(seeded_backend):
    """After search returns L3 results, use_count is incremented."""
    results = await seeded_backend.search("test_user", "deploy application", layers=["L3"])
    assert len(results) >= 1
    assert results[0].use_count == 1  # Was 0, now 1 after search

    # Search again
    results2 = await seeded_backend.search("test_user", "deploy application", layers=["L3"])
    assert results2[0].use_count == 2  # Incremented again


@pytest.mark.asyncio
async def test_search_sets_last_used_at(seeded_backend):
    """After search, last_used_at is set to current time."""
    before = time.time()
    results = await seeded_backend.search("test_user", "deploy application", layers=["L3"])
    after = time.time()
    assert results[0].last_used_at is not None
    assert before <= results[0].last_used_at <= after


@pytest.mark.asyncio
async def test_use_count_update_does_not_reindex_fts(seeded_backend):
    """Updating use_count/last_used_at should NOT trigger FTS5 reindex."""
    # Search to trigger the UPDATE
    await seeded_backend.search("test_user", "deploy application", layers=["L3"])

    # Verify the content is still searchable (FTS5 index intact)
    results = await seeded_backend.search("test_user", "deploy application", layers=["L3"])
    assert len(results) >= 1
    assert "deploy" in results[0].content.lower()


@pytest.mark.asyncio
async def test_content_update_still_triggers_fts_reindex(backend):
    """Updating content column SHOULD still trigger FTS5 reindex."""
    entry = MemoryEntry(
        id="proc_reindex",
        layer="L3",
        type="workflow",
        content="Original content about deploying servers",
        source_session_id="ses_test",
        created_at=time.time(),
        updated_at=time.time(),
        status="active",
    )
    await backend.store("test_user", entry)

    # Verify original content is searchable
    results = await backend.search("test_user", "deploying servers", layers=["L3"])
    assert len(results) >= 1

    entry.content = "Completely new content about building containers"
    entry.updated_at = time.time()
    await backend.store("test_user", entry)

    # Old content should NOT be findable
    results_old = await backend.search("test_user", "deploying servers", layers=["L3"])
    old_ids = [r.id for r in results_old]
    assert "proc_reindex" not in old_ids

    # New content SHOULD be findable
    results_new = await backend.search("test_user", "building containers", layers=["L3"])
    assert any(r.id == "proc_reindex" for r in results_new)
