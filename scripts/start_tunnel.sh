#!/bin/bash
# Pornește Cloudflare Tunnel-ul Kage (config real: cloudflare_tunnel.yaml), în background.
# Prerequisit: setup unic din cloudflare_tunnel.example.yaml + Mission Control pe :3001.
set -uo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
CONFIG="$SCRIPT_DIR/cloudflare_tunnel.yaml"
LOGS="$SCRIPT_DIR/.logs"
mkdir -p "$LOGS"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "  ✗ cloudflared nu e instalat — rulează: brew install cloudflared"
  exit 1
fi
if [ ! -f "$CONFIG" ]; then
  echo "  ✗ lipsește $CONFIG — copiază din cloudflare_tunnel.example.yaml și completează"
  exit 1
fi
if pgrep -f "cloudflared.*$CONFIG" >/dev/null 2>&1; then
  echo "  ✓ Cloudflare Tunnel (deja pornit)"
  exit 0
fi
if ! nc -z 127.0.0.1 3001 2>/dev/null; then
  echo "  ! Mission Control (:3001) nu rulează — pornește-l întâi cu ./scripts/start_frontend.sh"
fi

echo "  → Pornesc Cloudflare Tunnel..."
nohup cloudflared tunnel --config "$CONFIG" run >> "$LOGS/tunnel.log" 2>&1 &
sleep 3
pgrep -f "cloudflared.*$CONFIG" >/dev/null 2>&1 && echo "  ✓ Tunnel pornit (vezi $LOGS/tunnel.log)" || echo "  ✗ Tunnel — eroare, vezi $LOGS/tunnel.log"
