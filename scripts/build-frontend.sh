#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_ROOT/web"

if [ ! -d "node_modules" ]; then
  echo "📦 Installing dependencies..."
  npm install
fi

echo "🔨 Building frontend..."
npm run build

echo "✅ Built to web/dist/"
ls -lh dist/assets/
