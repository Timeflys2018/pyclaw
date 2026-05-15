from __future__ import annotations

import json

from pyclaw.core.session_export import render_session_json, render_session_markdown
from pyclaw.models import (
    CompactionEntry,
    CustomEntry,
    MessageEntry,
    ModelChangeEntry,
    SessionHeader,
    SessionTree,
    generate_entry_id,
    now_iso,
)
from pyclaw.models.agent import ImageBlock, TextBlock


def _new_tree() -> SessionTree:
    header = SessionHeader(
        id="sid-export", workspace_id="ws", agent_id="default", session_key="key"
    )
    return SessionTree(header=header)


def _append(tree: SessionTree, entry) -> None:
    tree.entries[entry.id] = entry
    tree.order.append(entry.id)
    tree.leaf_id = entry.id


def test_markdown_renders_user_and_assistant_messages() -> None:
    tree = _new_tree()
    user_id = generate_entry_id(set())
    _append(
        tree,
        MessageEntry(id=user_id, parent_id=None, timestamp=now_iso(), role="user", content="Hello"),
    )
    assistant_id = generate_entry_id({user_id})
    _append(
        tree,
        MessageEntry(
            id=assistant_id,
            parent_id=user_id,
            timestamp=now_iso(),
            role="assistant",
            content="Hi there!",
        ),
    )

    md = render_session_markdown(tree)

    assert "## USER" in md
    assert "Hello" in md
    assert "## ASSISTANT" in md
    assert "Hi there!" in md
    assert "# Session" in md


def test_markdown_renders_compaction_entry() -> None:
    tree = _new_tree()
    eid = generate_entry_id(set())
    _append(
        tree,
        CompactionEntry(
            id=eid,
            parent_id=None,
            timestamp=now_iso(),
            summary="Discussed X and Y",
            first_kept_entry_id="abcd1234",
            tokens_before=500,
        ),
    )

    md = render_session_markdown(tree)

    assert "## COMPACTION" in md
    assert "Discussed X and Y" in md
    assert "500" in md


def test_markdown_renders_model_change_entry() -> None:
    tree = _new_tree()
    eid = generate_entry_id(set())
    _append(
        tree,
        ModelChangeEntry(
            id=eid,
            parent_id=None,
            timestamp=now_iso(),
            provider="anthropic",
            model_id="claude-sonnet-4",
        ),
    )

    md = render_session_markdown(tree)

    assert "## MODEL_CHANGE" in md
    assert "anthropic" in md
    assert "claude-sonnet-4" in md


def test_markdown_renders_custom_entry() -> None:
    tree = _new_tree()
    eid = generate_entry_id(set())
    _append(
        tree,
        CustomEntry(
            id=eid,
            parent_id=None,
            timestamp=now_iso(),
            custom_type="external_audit",
            data={"checked_by": "compliance"},
        ),
    )

    md = render_session_markdown(tree)

    assert "## CUSTOM" in md
    assert "external_audit" in md
    assert '"checked_by"' in md


def test_markdown_pairs_tool_calls_with_tool_responses() -> None:
    tree = _new_tree()
    user_id = generate_entry_id(set())
    _append(
        tree,
        MessageEntry(
            id=user_id,
            parent_id=None,
            timestamp=now_iso(),
            role="user",
            content="run a tool",
        ),
    )
    asst_id = generate_entry_id({user_id})
    _append(
        tree,
        MessageEntry(
            id=asst_id,
            parent_id=user_id,
            timestamp=now_iso(),
            role="assistant",
            content="Calling tool",
            tool_calls=[
                {
                    "id": "tc-1",
                    "type": "function",
                    "function": {"name": "echo", "arguments": '{"text": "hi"}'},
                }
            ],
        ),
    )
    tool_id = generate_entry_id({user_id, asst_id})
    _append(
        tree,
        MessageEntry(
            id=tool_id,
            parent_id=asst_id,
            timestamp=now_iso(),
            role="tool",
            content="echo: hi",
            tool_call_id="tc-1",
        ),
    )

    md = render_session_markdown(tree)

    assert "Tool calls" in md
    assert "tc-1" in md
    assert "echo: hi" in md
    assert "## TOOL" not in md


def test_markdown_truncates_image_blocks_without_full_base64() -> None:
    tree = _new_tree()
    big_image_data = "A" * 5000
    eid = generate_entry_id(set())
    _append(
        tree,
        MessageEntry(
            id=eid,
            parent_id=None,
            timestamp=now_iso(),
            role="user",
            content=[
                ImageBlock(data=big_image_data, mime_type="image/png"),
                TextBlock(text="describe this"),
            ],
        ),
    )

    md = render_session_markdown(tree)

    assert "image/png" in md
    assert "describe this" in md
    assert big_image_data not in md


def test_json_serialization_round_trips() -> None:
    tree = _new_tree()
    eid = generate_entry_id(set())
    _append(
        tree,
        MessageEntry(
            id=eid,
            parent_id=None,
            timestamp=now_iso(),
            role="user",
            content="hi",
        ),
    )

    rendered = render_session_json(tree)
    parsed = json.loads(rendered)

    assert parsed["header"]["id"] == "sid-export"
    assert eid in parsed["entries"]


def test_json_includes_model_override_field_when_set() -> None:
    tree = _new_tree()
    tree.header = tree.header.model_copy(update={"model_override": "x-model"})

    rendered = render_session_json(tree)
    parsed = json.loads(rendered)

    assert parsed["header"]["model_override"] == "x-model"
