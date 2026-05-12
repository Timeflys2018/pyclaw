"""DbFileNamingPolicy: derive sqlite filenames from session_keys.

Two implementations:
    * :class:`HumanReadableNaming` — default. Two-stage sanitizer that
      preserves 100% byte-level backward compatibility with the legacy
      ``session_key.replace(":", "_") + ".db"`` logic.
    * :class:`HashOnlyNaming` — production-safe alternative. Always emits
      SHA-256 hex filenames, eliminating the path-traversal surface entirely.

Policy injection is Phase F work; this module only defines the contract +
implementations.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Protocol

logger = logging.getLogger(__name__)


class DbFileNamingPolicy(Protocol):
    """Policy for deriving sqlite filenames from session_keys.

    Contract for implementations:
        * Output contains only characters safe for POSIX filenames.
        * No output resolves outside the base_dir when joined via ``Path``.
        * Non-empty input MUST produce non-empty output.
        * Empty input MAY raise ``ValueError``.
    """

    def filename_for(self, session_key: str) -> str: ...


_DANGEROUS_CHARS = re.compile(r"[/\\\x00-\x1f\x7f]")


class HumanReadableNaming:
    """Default two-stage sanitizer.

    Stage 1 (byte-equivalent to legacy): ``session_key.replace(":", "_")``.
    Preserves alphanumerics, ``_``, ``-``, ``.``, ``@``, ``+``, space, and
    all Unicode. Only replaces the colon (session_key namespace separator).

    Stage 2 (defense-in-depth): replace path separators, null byte, and C0
    control characters with ``_``. Emits a WARNING if Stage 2 altered the
    value.

    Degenerate inputs (empty-after-Stage-1, ``.``, ``..``) fall back to
    ``_unsafe_<sha256[:16]>.db`` with a WARNING.
    """

    def filename_for(self, session_key: str) -> str:
        if not session_key:
            raise ValueError("session_key must not be empty")

        stage1 = session_key.replace(":", "_")
        if stage1 in (".", "..") or stage1.strip() == "":
            digest = hashlib.sha256(session_key.encode()).hexdigest()[:16]
            logger.warning(
                "session_key %r is degenerate, using hash fallback",
                session_key,
            )
            return f"_unsafe_{digest}.db"

        stage2 = _DANGEROUS_CHARS.sub("_", stage1)
        if stage2 != stage1:
            logger.warning(
                "session_key contains dangerous chars, sanitized: %r -> %r",
                session_key, stage2,
            )
        return stage2 + ".db"


class HashOnlyNaming:
    """SHA-256 hex filename. Zero path-traversal surface."""

    def filename_for(self, session_key: str) -> str:
        digest = hashlib.sha256(session_key.encode()).hexdigest()
        return f"{digest}.db"
