"""RunControl: out-of-band control surface for an in-flight agent run.

The minimal v1 contract intentionally exposes ONLY two fields (``abort_event``
and ``active``). Future runtime-control needs (steer buffers, side-channel
queues, etc.) MUST be added when their concrete consumers land — never
predicted. This avoids the ``CommandContext.abort_event`` dead-field mistake
we explicitly documented in the change proposal.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class RunControl:
    """Out-of-band control surface for an in-flight agent run.

    Fields
    ------
    abort_event:
        Signals the runner to terminate ASAP. Channels and ProtocolOp
        handlers SHALL call :meth:`stop` to set it. The runner itself uses
        ``abort_event.is_set()`` (via ``runtime_util.is_abort_set``) at every
        await boundary.
    active:
        Lifecycle flag maintained EXCLUSIVELY by the channel adapter via
        try/finally around ``run_agent_stream(...)``. Hooks SHALL NOT mutate
        this field — see ``on_run_start`` / ``on_run_end`` design note.
    """

    abort_event: asyncio.Event = field(default_factory=asyncio.Event)
    active: bool = False

    def stop(self) -> None:
        self.abort_event.set()

    def is_active(self) -> bool:
        return self.active and not self.abort_event.is_set()
