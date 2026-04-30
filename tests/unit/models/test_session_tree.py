from __future__ import annotations

import pytest

from pyclaw.models import (
    CompactionEntry,
    MessageEntry,
    SessionHeader,
    SessionTree,
    generate_entry_id,
)


@pytest.fixture
def tree() -> SessionTree:
    header = SessionHeader(id="session-1", workspace_id="default", agent_id="main")
    return SessionTree(header=header)


def _msg(tree: SessionTree, role: str, content: str, parent: str | None = None) -> MessageEntry:
    entry = MessageEntry(
        id=generate_entry_id(tree.all_entry_ids()),
        parent_id=parent if parent is not None else tree.leaf_id,
        role=role,
        content=content,
    )
    tree.append(entry)
    return entry


class TestSessionTreeAppend:
    def test_append_sets_leaf(self, tree: SessionTree) -> None:
        entry = _msg(tree, "user", "hello")
        assert tree.leaf_id == entry.id

    def test_sequential_appends_form_chain(self, tree: SessionTree) -> None:
        m1 = _msg(tree, "user", "hi")
        m2 = _msg(tree, "assistant", "hello")
        m3 = _msg(tree, "user", "how are you")

        assert m2.parent_id == m1.id
        assert m3.parent_id == m2.id
        assert tree.leaf_id == m3.id

    def test_duplicate_id_raises(self, tree: SessionTree) -> None:
        entry = MessageEntry(id="abc", parent_id=None, role="user", content="hi")
        tree.append(entry)
        duplicate = MessageEntry(id="abc", parent_id=None, role="user", content="again")
        with pytest.raises(ValueError):
            tree.append(duplicate)


class TestGetBranch:
    def test_empty_tree_returns_empty(self, tree: SessionTree) -> None:
        assert tree.get_branch() == []

    def test_returns_root_to_leaf_order(self, tree: SessionTree) -> None:
        m1 = _msg(tree, "user", "one")
        m2 = _msg(tree, "assistant", "two")
        m3 = _msg(tree, "user", "three")

        branch = tree.get_branch()
        assert [e.id for e in branch] == [m1.id, m2.id, m3.id]


class TestBuildSessionContext:
    def test_no_compaction_includes_all_messages(self, tree: SessionTree) -> None:
        _msg(tree, "user", "hi")
        _msg(tree, "assistant", "hello")
        _msg(tree, "user", "bye")

        messages = tree.build_session_context()
        assert len(messages) == 3
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "hi"
        assert messages[2]["content"] == "bye"

    def test_compaction_replaces_old_messages(self, tree: SessionTree) -> None:
        m1 = _msg(tree, "user", "one")
        _msg(tree, "assistant", "two")
        m3 = _msg(tree, "user", "three")
        _msg(tree, "assistant", "four")

        comp = CompactionEntry(
            id=generate_entry_id(tree.all_entry_ids()),
            parent_id=tree.leaf_id,
            summary="user talked about stuff",
            first_kept_entry_id=m3.id,
            tokens_before=100,
        )
        tree.append(comp)
        _msg(tree, "user", "five")

        messages = tree.build_session_context()
        assert len(messages) == 4
        assert "summary" in messages[0]["content"].lower() or "user talked" in messages[0]["content"]
        assert messages[0]["role"] == "assistant"
        assert messages[1]["content"] == "three"
        assert messages[3]["content"] == "five"


class TestEntryIdGeneration:
    def test_generates_8_char_hex(self) -> None:
        ids = {generate_entry_id(set()) for _ in range(10)}
        assert all(len(i) == 8 for i in ids)
        assert all(all(c in "0123456789abcdef" for c in i) for i in ids)

    def test_avoids_collisions(self) -> None:
        existing = {"aabbccdd"}
        for _ in range(50):
            new_id = generate_entry_id(existing)
            assert new_id not in existing
            existing.add(new_id)
