#!/usr/bin/env bash
# Faza 4 — pornește status widget în menubar
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/.widget-venv/bin/python3.12"
WIDGET="$SCRIPT_DIR/status_widget.py"
PIDFILE="$SCRIPT_DIR/.widget.pid"
LOG="$SCRIPT_DIR/.widget.log"

# Oprește instanța precedentă dacă există
if [[ -f "$PIDFILE" ]]; then
  OLD_PID=$(cat "$PIDFILE")
  if kill -0 "$OLD_PID" 2>/dev/null; then
    echo "Opresc widget-ul anterior (PID $OLD_PID)..."
    kill "$OLD_PID"
    sleep 1
  fi
  rm -f "$PIDFILE"
fi

echo "Pornesc Orchestrator Status Widget..."
nohup "$PYTHON" "$WIDGET" > "$LOG" 2>&1 &
echo $! > "$PIDFILE"
echo "Widget pornit (PID $(cat "$PIDFILE")). Log: $LOG"
