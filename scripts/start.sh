#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

# Check Python venv
if [ ! -f ".venv/bin/python" ]; then
  echo "❌ .venv not found. Run: python3 -m venv .venv && pip install -e ."
  exit 1
fi

# Check if web/dist exists, build if not
if [ ! -d "web/dist" ]; then
  echo "📦 Building frontend..."
  (cd web && npm install && npm run build)
fi

# Check Redis (optional)
if command -v redis-cli &>/dev/null && redis-cli ping &>/dev/null 2>&1; then
  echo "✅ Redis: connected"
else
  echo "⚠️  Redis: not available (using in-memory sessions)"
fi

echo "🐾 Starting PyClaw on http://localhost:8000"
exec .venv/bin/uvicorn pyclaw.app:create_app --factory --host 0.0.0.0 --port 8000 --reload
