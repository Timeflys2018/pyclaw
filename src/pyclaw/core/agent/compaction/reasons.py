from __future__ import annotations

from pyclaw.models.agent import CompactionReasonCode

ALL_REASON_CODES: tuple[CompactionReasonCode, ...] = (
    "compacted",
    "no_compactable_entries",
    "below_threshold",
    "already_compacted_recently",
    "live_context_still_exceeds_target",
    "guard_blocked",
    "summary_failed",
    "timeout",
    "aborted",
    "provider_error_4xx",
    "provider_error_5xx",
    "unknown",
)


def classify_compaction_reason(text: str | None) -> CompactionReasonCode:
    if not text:
        return "unknown"

    lowered = text.lower()

    if "already compacted" in lowered or "recent compaction" in lowered:
        return "already_compacted_recently"

    if "below threshold" in lowered or "within-budget" in lowered or "within budget" in lowered:
        return "below_threshold"

    if "no compactable" in lowered or "no-safe-cut" in lowered or "nothing to compact" in lowered:
        return "no_compactable_entries"

    if "guard" in lowered and ("block" in lowered or "policy" in lowered):
        return "guard_blocked"

    if "summary" in lowered and ("fail" in lowered or "error" in lowered):
        return "summary_failed"

    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"

    if "abort" in lowered or "cancel" in lowered:
        return "aborted"

    if ("still exceeds" in lowered or "over budget" in lowered) and "context" in lowered:
        return "live_context_still_exceeds_target"

    if "4" in lowered and ("provider" in lowered or "api" in lowered) and "error" in lowered:
        return "provider_error_4xx"

    if "5" in lowered and ("provider" in lowered or "api" in lowered) and "error" in lowered:
        return "provider_error_5xx"

    if lowered.startswith("compacted-at-") or lowered == "compacted":
        return "compacted"

    return "unknown"
