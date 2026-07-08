#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

cd "$ROOT_DIR"
cd "$ROOT_DIR/apps/web"
npm run build

cd "$ROOT_DIR"
exec "$ROOT_DIR/metaclass_env/bin/uvicorn" metaclass.main:app \
  --host 127.0.0.1 \
  --port 8000
