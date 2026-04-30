from __future__ import annotations

from pyclaw.core.agent.compaction.checkpoint import (
    CompactionCheckpoint,
    take_checkpoint,
)
from pyclaw.core.agent.compaction.dedup import (
    DEFAULT_MIN_CHARS,
    DEFAULT_WINDOW_SECONDS,
    dedupe_duplicate_user_messages,
    normalize_for_dedup,
)
from pyclaw.core.agent.compaction.hardening import (
    HARDENED_SUMMARIZER_SYSTEM_PROMPT,
    IDENTIFIER_PRESERVATION_INSTRUCTIONS,
    filter_oversized_messages,
    has_real_conversation,
    sanity_check_token_estimate,
    split_into_chunks,
    strip_tool_result_details,
    summarize_in_stages,
)
from pyclaw.core.agent.compaction.planning import (
    DEFAULT_COMPACTION_SAFETY_TIMEOUT_S,
    DEFAULT_KEEP_RECENT_TOKENS,
    DEFAULT_THRESHOLD,
    SUMMARIZER_SYSTEM_PROMPT,
    CompactionPlan,
    build_summarizer_payload,
    compact_with_safety_timeout,
    estimate_message_tokens,
    estimate_messages_tokens,
    estimate_tokens,
    find_cut_point,
    plan_compaction,
    should_compact,
)
from pyclaw.core.agent.compaction.reasons import (
    ALL_REASON_CODES,
    classify_compaction_reason,
)

__all__ = [
    "ALL_REASON_CODES",
    "CompactionCheckpoint",
    "CompactionPlan",
    "DEFAULT_COMPACTION_SAFETY_TIMEOUT_S",
    "DEFAULT_KEEP_RECENT_TOKENS",
    "DEFAULT_MIN_CHARS",
    "DEFAULT_THRESHOLD",
    "DEFAULT_WINDOW_SECONDS",
    "HARDENED_SUMMARIZER_SYSTEM_PROMPT",
    "IDENTIFIER_PRESERVATION_INSTRUCTIONS",
    "SUMMARIZER_SYSTEM_PROMPT",
    "build_summarizer_payload",
    "classify_compaction_reason",
    "compact_with_safety_timeout",
    "dedupe_duplicate_user_messages",
    "estimate_message_tokens",
    "estimate_messages_tokens",
    "estimate_tokens",
    "filter_oversized_messages",
    "find_cut_point",
    "has_real_conversation",
    "normalize_for_dedup",
    "plan_compaction",
    "sanity_check_token_estimate",
    "should_compact",
    "split_into_chunks",
    "strip_tool_result_details",
    "summarize_in_stages",
    "take_checkpoint",
]
