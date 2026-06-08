#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'

ok()   { echo -e "${GREEN}  ✓${NC} $1"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $1"; }
fail() { echo -e "${RED}  ✗${NC} $1"; }

echo ""
echo "=== Kage Setup ==="
echo ""

# Python 3.10+
if ! command -v python3 &>/dev/null; then
    fail "Python 3 not found. Install from https://python.org"
    exit 1
fi
PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    fail "Python 3.10+ required (found $PY_VER)"
    exit 1
fi
ok "Python $PY_VER"

# Ollama
if command -v ollama &>/dev/null; then
    ok "Ollama found"
else
    warn "Ollama not found — install from https://ollama.ai (required for local AI tiers)"
fi

# Claude CLI
if command -v claude &>/dev/null; then
    ok "Claude CLI found: $(which claude)"
else
    warn "Claude CLI not found — install with: npm install -g @anthropic-ai/claude-code"
    warn "  (required for !run and autonomous task features)"
fi

# Gemini CLI
if command -v gemini &>/dev/null; then
    ok "Gemini CLI found: $(which gemini)"
else
    warn "Gemini CLI not found — optional, needed for Gemini agent tasks"
fi

# Virtual environment
if [ ! -d "$DIR/.venv" ]; then
    echo "  → Creating Python virtual environment..."
    python3 -m venv "$DIR/.venv"
    ok ".venv created"
else
    ok ".venv exists"
fi

# Install dependencies
echo "  → Installing Python dependencies..."
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
ok "Dependencies installed"

# risk_settings.json (generated with correct absolute paths)
if [ ! -f "$DIR/risk_settings.json" ]; then
    sed "s|KAGE_DIR|$DIR|g" "$DIR/risk_settings.example.json" > "$DIR/risk_settings.json"
    ok "risk_settings.json generated"
else
    ok "risk_settings.json exists"
fi

# kage_config.json
if [ ! -f "$DIR/kage_config.json" ]; then
    cp "$DIR/kage_config.example.json" "$DIR/kage_config.json"
    echo ""
    warn "Created kage_config.json from example — EDIT IT before starting:"
    warn "  - ntfy_topic: pick a unique name (e.g. kage-yourname-abc123)"
    warn "  - api_token:  run 'openssl rand -hex 20' and paste the result"
    warn "  - vault_path: path to your Obsidian vault (or any folder)"
    echo ""
else
    ok "kage_config.json exists"
fi

# Ollama model
if command -v ollama &>/dev/null; then
    if ollama list 2>/dev/null | grep -q "qwen"; then
        ok "Qwen model available"
    else
        warn "No Qwen model found — pull with: ollama pull qwen3:8b"
    fi
fi

echo ""
echo "=== Setup complete ==="
echo ""
echo "  Edit kage_config.json, then start with:"
echo "    bash start_all.sh"
echo ""
echo "  Kage will open at: http://localhost:4001/chat"
echo ""
