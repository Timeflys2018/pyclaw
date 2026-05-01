from __future__ import annotations

import logging

from pyclaw.storage.workspace.base import WorkspaceStore

logger = logging.getLogger(__name__)

BOOTSTRAP_FILE_WARN_BYTES = 10_240


async def load_bootstrap_context(
    workspace_id: str,
    workspace_store: WorkspaceStore,
    filenames: list[str],
) -> str:
    found: list[tuple[str, str]] = []
    for filename in filenames:
        content = await workspace_store.get_file(workspace_id, filename)
        if content is not None and content.strip():
            found.append((filename, content))

    if not found:
        return ""

    total_bytes = sum(len(c.encode()) for _, c in found)
    if total_bytes > BOOTSTRAP_FILE_WARN_BYTES:
        logger.warning(
            "bootstrap context for workspace %r is large (%d bytes); consider trimming bootstrap files",
            workspace_id,
            total_bytes,
        )

    if len(found) == 1:
        return found[0][1]

    parts: list[str] = []
    for i, (filename, content) in enumerate(found):
        if i == 0:
            parts.append(content)
        else:
            parts.append(f"## {filename}\n\n{content}")
    return "\n\n".join(parts)
