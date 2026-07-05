#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
exec "$SCRIPT_DIR/.venv/bin/litellm" \
  --config "$SCRIPT_DIR/litellm_config.yaml" \
  --port 4000 \
  --host 0.0.0.0
