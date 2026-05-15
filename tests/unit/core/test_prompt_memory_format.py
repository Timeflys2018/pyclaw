"""Tests for memory_context format and L1 snapshot filtering."""


class TestFormatMemoryContext:
    """Task 5.1: memory_context includes entry_id prefix."""

    def test_l3_entry_includes_short_id(self):
        from pyclaw.core.context_engine import DefaultContextEngine

        class MockEntry:
            id = "7f3a2b9c-1234-5678-abcd-ef0123456789"
            layer = "L3"
            type = "auto_sop"
            content = "Deploy via helm chart"

        result = DefaultContextEngine._format_memory_context([MockEntry()], [])
        assert result is not None
        assert "[auto_sop|7f3a2b9c]" in result
        assert "Deploy via helm chart" in result

    def test_l2_entry_no_id_change(self):
        from pyclaw.core.context_engine import DefaultContextEngine

        class MockEntry:
            id = "abc12345-xxxx"
            layer = "L2"
            type = "user_preference"
            content = "Prefers dark mode"

        result = DefaultContextEngine._format_memory_context([MockEntry()], [])
        assert result is not None
        # L2 should still use old format (no ID prefix)
        assert "[user_preference]" in result

    def test_empty_id_handled(self):
        from pyclaw.core.context_engine import DefaultContextEngine

        class MockEntry:
            id = ""
            layer = "L3"
            type = "workflow"
            content = "Some procedure"

        result = DefaultContextEngine._format_memory_context([MockEntry()], [])
        assert result is not None
        assert "[workflow|]" in result


class TestFormatL1Snapshot:
    """Task 5.2: L1 snapshot filters non-active entries."""

    def test_active_entries_rendered(self):
        from pyclaw.core.agent.runner import _format_l1_snapshot

        class MockEntry:
            content = "Some memory"
            status = "active"

        result = _format_l1_snapshot([MockEntry()])
        assert "<memory_index>" in result
        assert "Some memory" in result

    def test_archived_entries_filtered(self):
        from pyclaw.core.agent.runner import _format_l1_snapshot

        class MockActive:
            content = "Active memory"
            status = "active"

        class MockArchived:
            content = "Archived memory"
            status = "archived"

        result = _format_l1_snapshot([MockActive(), MockArchived()])
        assert "Active memory" in result
        assert "Archived memory" not in result

    def test_all_archived_returns_only_tags(self):
        from pyclaw.core.agent.runner import _format_l1_snapshot

        class MockArchived:
            content = "Old stuff"
            status = "archived"

        result = _format_l1_snapshot([MockArchived()])
        assert "<memory_index>" in result
        assert "Old stuff" not in result

    def test_dict_entries_filtered(self):
        from pyclaw.core.agent.runner import _format_l1_snapshot

        entries = [
            {"content": "Active", "status": "active"},
            {"content": "Archived", "status": "archived"},
        ]
        result = _format_l1_snapshot(entries)
        assert "Active" in result
        assert "Archived" not in result
