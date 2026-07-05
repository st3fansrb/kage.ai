#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$SCRIPT_DIR/.venv/bin/uvicorn" orchestrator:app \
  --host 0.0.0.0 \
  --port 4001 \
  --app-dir "$SCRIPT_DIR"
