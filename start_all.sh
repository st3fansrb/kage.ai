#!/usr/bin/env bash
# AI Orchestration System v2 — pornește toate serviciile în background.
# Idempotent: dacă un serviciu e deja pornit pe portul lui, îl sare.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LOGS="$DIR/.logs"
mkdir -p "$LOGS"

port_up() { nc -z 127.0.0.1 "$1" 2>/dev/null; }

echo "=== AI Orchestration System v2 ==="
echo ""

# ── Ollama ────────────────────────────────────────────────────────────────────
if port_up 11434; then
  echo "  ✓ Ollama         :11434"
else
  echo "  → Pornesc Ollama (RAM opt: 5m keep-alive)..."
  export OLLAMA_KEEP_ALIVE="5m"
  nohup ollama serve >> "$LOGS/ollama.log" 2>&1 &
  sleep 2
  port_up 11434 && echo "  ✓ Ollama pornit" || echo "  ✗ Ollama — eroare, vezi $LOGS/ollama.log"
fi

# ── Persistent Sleep Prevention ───────────────────────────────────────────────
if pgrep caffeinate > /dev/null; then
  echo "  ✓ Anti-sleep active"
else
  echo "  → Activăm persistent anti-sleep..."
  nohup caffeinate -d -i > /dev/null 2>&1 &
fi

# ── LiteLLM :4000 ─────────────────────────────────────────────────────────────
if port_up 4000; then
  echo "  ✓ LiteLLM        :4000"
else
  echo "  → Pornesc LiteLLM..."
  nohup "$DIR/.venv/bin/litellm" \
    --config "$DIR/litellm_config.yaml" \
    --port 4000 --host 0.0.0.0 \
    >> "$LOGS/litellm.log" 2>&1 &
  sleep 3
  port_up 4000 && echo "  ✓ LiteLLM pornit" || echo "  ✗ LiteLLM — eroare, vezi $LOGS/litellm.log"
fi

# ── Orchestrator :4001 ────────────────────────────────────────────────────────
if port_up 4001; then
  echo "  ✓ Orchestrator   :4001"
else
  echo "  → Pornesc Orchestrator..."
  nohup "$DIR/.venv/bin/uvicorn" orchestrator:app \
    --host 0.0.0.0 --port 4001 \
    --app-dir "$DIR" \
    >> "$LOGS/orchestrator.log" 2>&1 &
  sleep 2
  port_up 4001 && echo "  ✓ Orchestrator pornit" || echo "  ✗ Orchestrator — eroare, vezi $LOGS/orchestrator.log"
fi

# ── Browser (indiferent dacă tocmai l-am pornit sau era deja up)
if [[ "$*" != *"--no-browser"* ]]; then
  echo "  → Deschid http://localhost:4001/chat ..."
  open "http://localhost:4001/chat"
fi

echo ""
echo "=== Sistem pornit. Kage: http://localhost:4001/chat ==="
