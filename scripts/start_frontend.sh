#!/bin/bash
# Pornește Mission Control (Next.js) în producție pe :3001, în background.
# Idempotent: dacă portul e ocupat, sare. Buildul se face o singură dată (dacă .next lipsește).
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
FE="$SCRIPT_DIR/frontend"
LOGS="$SCRIPT_DIR/.logs"
mkdir -p "$LOGS"

if nc -z 127.0.0.1 3001 2>/dev/null; then
  echo "  ✓ Mission Control :3001 (deja pornit)"
  exit 0
fi

if [ ! -f "$FE/.env.local" ]; then
  echo "  ✗ lipsește frontend/.env.local — copiază din .env.local.example și completează KAGE_API_TOKEN"
  exit 1
fi

cd "$FE" || exit 1
if [ ! -d node_modules ]; then
  echo "  → npm install (prima rulare)..."
  npm install >> "$LOGS/frontend.log" 2>&1 || { echo "  ✗ npm install a eșuat, vezi $LOGS/frontend.log"; exit 1; }
fi
if [ ! -d .next ]; then
  echo "  → next build (prima rulare)..."
  npm run build >> "$LOGS/frontend.log" 2>&1 || { echo "  ✗ next build a eșuat, vezi $LOGS/frontend.log"; exit 1; }
fi

echo "  → Pornesc Mission Control (:3001)..."
nohup npm run start >> "$LOGS/frontend.log" 2>&1 &
sleep 3
nc -z 127.0.0.1 3001 2>/dev/null && echo "  ✓ Mission Control pornit" || echo "  ✗ Mission Control — eroare, vezi $LOGS/frontend.log"
