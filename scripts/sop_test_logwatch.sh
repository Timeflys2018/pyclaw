#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_FILE="${1:-${PROJECT_ROOT}/logs/pyclaw.log}"

if [[ ! -f "$LOG_FILE" ]]; then
  echo "❌ Log file not found: $LOG_FILE"
  echo ""
  echo "Usage:"
  echo "  $0 [path/to/pyclaw.log]"
  echo ""
  echo "If your server runs in foreground (./scripts/start.sh), restart it as:"
  echo "  mkdir -p logs"
  echo "  ./scripts/start.sh 2>&1 | tee logs/pyclaw.log"
  echo ""
  echo "Then run this script in another terminal."
  exit 1
fi

echo "🔍 Watching SOP extraction events in: $LOG_FILE"
echo "─────────────────────────────────────────────────────────────────"
echo "Legend:"
echo "  ✅ extract_sop: session=...   → extraction completed"
echo "  ⏭  no candidates / skipping  → skipped (empty or below threshold)"
echo "  🔒 already in progress       → SETNX lock held by another extraction"
echo "  ❌ SOP rejected:             → _validate_sop blocked an SOP"
echo "  🔁 LLM call/parse failed     → retry attempt"
echo "  📥 SopCandidateTracker       → candidate accumulation events"
echo "─────────────────────────────────────────────────────────────────"
echo ""

exec tail -F "$LOG_FILE" | grep --line-buffered -E \
  "(extract_sop|SOP rejected|SopCandidateTracker|sop extraction (skipped|already)|maybe_spawn|LLM (call|output) (failed|parse)|nudge counter|dedup search|rate limit check)"
