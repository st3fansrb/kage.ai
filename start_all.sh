#!/usr/bin/env bash
# AI Orchestration System v2 — pornește toate serviciile în background.
# Idempotent: dacă un serviciu e deja pornit pe portul lui, îl sare.
set -uo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
LOGS="$DIR/.logs"
mkdir -p "$LOGS"

# ── PATH ──────────────────────────────────────────────────────────────────────
# launchd pornește cu un PATH minimal (/usr/bin:/bin:/usr/sbin:/sbin), FĂRĂ
# /opt/homebrew/bin — deci binarele instalate cu brew (ffmpeg, whisper-cli, ollama,
# yt-dlp) nu se găsesc după reboot, doar la pornire manuală din terminal. Prepend
# Homebrew (Apple Silicon + Intel) ca toate serviciile copil să le vadă uniform.
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# ── yt-dlp la zi (WP-V) ───────────────────────────────────────────────────────
# Site-urile video (TikTok etc.) se strică periodic la anti-bot; yt-dlp ține pasul
# doar actualizat. Upgrade best-effort în .venv, în FUNDAL, throttled la max o dată/24h
# (stamp file), NEBLOCANT — fără rețea la boot pur și simplu sare, nu afectează pornirea.
_YTDLP_STAMP="$LOGS/.ytdlp_last_upgrade"
if [[ -x "$DIR/.venv/bin/python" ]] && { [[ ! -f "$_YTDLP_STAMP" ]] || [[ -n "$(find "$_YTDLP_STAMP" -mmin +1440 2>/dev/null)" ]]; }; then
  ( "$DIR/.venv/bin/python" -m pip install -q -U yt-dlp >> "$LOGS/ytdlp_upgrade.log" 2>&1 \
      && touch "$_YTDLP_STAMP" ) &
fi

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

# ── Mission Control (Next.js) :3001 ───────────────────────────────────────────
# UI-ul nou (WP10). Non-fatal: dacă lipsește .env.local sau node, sare fără să oprească restul.
if [[ "$*" != *"--no-ui"* ]]; then
  if port_up 3001; then
    echo "  ✓ Mission Control :3001"
  elif [[ -f "$DIR/frontend/.env.local" ]] && command -v npm >/dev/null 2>&1; then
    bash "$DIR/scripts/start_frontend.sh" || true
  else
    echo "  ! Mission Control sărit (lipsă frontend/.env.local sau npm) — vezi docs/INSTALL.md"
  fi
fi

# ── Browser (indiferent dacă tocmai l-am pornit sau era deja up)
if [[ "$*" != *"--no-browser"* ]]; then
  echo "  → Deschid http://localhost:3001 (Mission Control) ..."
  open "http://localhost:3001"
fi

echo ""
echo "=== Sistem pornit. Kage: http://localhost:3001 (Mission Control) ==="
