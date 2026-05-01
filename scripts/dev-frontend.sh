#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT/web"

if [ ! -d "node_modules" ]; then
  echo "📦 Installing frontend dependencies..."
  npm install
fi

echo "🌐 Starting Vite dev server on http://localhost:5173"
echo "   (proxies API requests to http://localhost:8000)"
exec npm run dev
