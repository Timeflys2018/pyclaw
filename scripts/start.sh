#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT"

export LITELLM_LOCAL_MODEL_COST_MAP=True
export LITELLM_LOG=ERROR

if [ ! -f ".venv/bin/python" ]; then
  echo "🐍 Creating virtual environment..."
  python3 -m venv .venv
  echo "📦 Installing dependencies..."
  .venv/bin/pip install -e ".[dev]" --quiet
fi

if [ ! -d "web/dist" ]; then
  echo "🌐 Building frontend..."
  (cd web && npm install && npm run build)
fi

# Check Redis (optional)
if command -v redis-cli &>/dev/null && redis-cli ping &>/dev/null 2>&1; then
  echo "✅ Redis: connected"
else
  echo "⚠️  Redis: not available (using in-memory sessions)"
fi

echo "🐾 Starting PyClaw on http://localhost:8000"
exec .venv/bin/python -m pyclaw.app
