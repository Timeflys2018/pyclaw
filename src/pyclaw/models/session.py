from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

from pyclaw.models.agent import AgentMessageDict, ContentBlock


def generate_entry_id(existing_ids: set[str], max_attempts: int = 100) -> str:
    for _ in range(max_attempts):
        candidate = secrets.token_hex(4)
        if candidate not in existing_ids:
            return candidate
    return secrets.token_hex(16)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionHeader(BaseModel):
    type: Literal["session"] = "session"
    version: int = 3
    id: str
    workspace_id: str
    agent_id: str
    created_at: str = Field(default_factory=now_iso)
    parent_session: str | None = None


class SessionEntryBase(BaseModel):
    id: str
    parent_id: str | None
    timestamp: str = Field(default_factory=now_iso)


class MessageEntry(SessionEntryBase):
    type: Literal["message"] = "message"
    role: Literal["user", "assistant", "tool"]
    content: str | list[ContentBlock]
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None


class CompactionEntry(SessionEntryBase):
    type: Literal["compaction"] = "compaction"
    summary: str
    first_kept_entry_id: str
    tokens_before: int
    details: dict[str, Any] | None = None


class ModelChangeEntry(SessionEntryBase):
    type: Literal["model_change"] = "model_change"
    provider: str
    model_id: str


class CustomEntry(SessionEntryBase):
    type: Literal["custom"] = "custom"
    custom_type: str
    data: dict[str, Any] | None = None


SessionEntry = Annotated[
    Union[MessageEntry, CompactionEntry, ModelChangeEntry, CustomEntry],
    Field(discriminator="type"),
]


class SessionTree(BaseModel):
    header: SessionHeader
    entries: dict[str, SessionEntry] = Field(default_factory=dict)
    order: list[str] = Field(default_factory=list)
    leaf_id: str | None = None

    def append(self, entry: SessionEntry) -> str:
        if entry.id in self.entries:
            raise ValueError(f"entry id {entry.id} already exists")
        self.entries[entry.id] = entry
        self.order.append(entry.id)
        self.leaf_id = entry.id
        return entry.id

    def get_entry(self, entry_id: str) -> SessionEntry | None:
        return self.entries.get(entry_id)

    def get_children(self, parent_id: str | None) -> list[SessionEntry]:
        return [e for e in self.entries.values() if e.parent_id == parent_id]

    def get_branch(self, from_id: str | None = None) -> list[SessionEntry]:
        start = from_id or self.leaf_id
        if start is None:
            return []
        path: list[SessionEntry] = []
        current: str | None = start
        while current is not None:
            entry = self.entries.get(current)
            if entry is None:
                break
            path.append(entry)
            current = entry.parent_id
        path.reverse()
        return path

    def build_session_context(self) -> list[AgentMessageDict]:
        branch = self.get_branch()
        latest_compaction_idx: int | None = None
        for i, entry in enumerate(branch):
            if isinstance(entry, CompactionEntry):
                latest_compaction_idx = i

        messages: list[AgentMessageDict] = []
        if latest_compaction_idx is not None:
            compaction = branch[latest_compaction_idx]
            assert isinstance(compaction, CompactionEntry)
            messages.append(
                {
                    "role": "assistant",
                    "content": f"[Previous conversation summary]\n{compaction.summary}",
                }
            )
            first_kept_idx: int | None = None
            for i, entry in enumerate(branch):
                if entry.id == compaction.first_kept_entry_id:
                    first_kept_idx = i
                    break
            start_idx = first_kept_idx if first_kept_idx is not None else latest_compaction_idx + 1
            tail = branch[start_idx:]
            for entry in tail:
                if isinstance(entry, MessageEntry) and entry.id != compaction.id:
                    messages.append(_message_entry_to_dict(entry))
        else:
            for entry in branch:
                if isinstance(entry, MessageEntry):
                    messages.append(_message_entry_to_dict(entry))

        return messages

    def all_entry_ids(self) -> set[str]:
        return set(self.entries.keys())


def _message_entry_to_dict(entry: MessageEntry) -> AgentMessageDict:
    content: Any
    if entry.role == "tool" and isinstance(entry.content, str):
        content = [{"type": "text", "text": entry.content}]
    elif isinstance(entry.content, list):
        content = _content_blocks_to_llm(entry.content)
    else:
        content = entry.content
    msg: AgentMessageDict = {"role": entry.role, "content": content}
    if entry.tool_calls:
        msg["tool_calls"] = entry.tool_calls
    if entry.tool_call_id:
        msg["tool_call_id"] = entry.tool_call_id
    return msg


def _content_blocks_to_llm(blocks: list[ContentBlock]) -> list[dict[str, Any]]:
    from pyclaw.models.agent import ImageBlock, TextBlock

    result: list[dict[str, Any]] = []
    for block in blocks:
        if isinstance(block, ImageBlock):
            result.append({
                "type": "image_url",
                "image_url": {"url": f"data:{block.mime_type};base64,{block.data}"},
            })
        elif isinstance(block, TextBlock):
            result.append({"type": "text", "text": block.text})
    return result
