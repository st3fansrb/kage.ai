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
    warn "  - models:     adjust tier→model mapping if you have different models"
    echo ""
else
    ok "kage_config.json exists"
fi

# litellm_config.yaml (generated from kage_config.json models section)
if [ ! -f "$DIR/litellm_config.yaml" ]; then
    "$DIR/.venv/bin/python3" - "$DIR" <<'PYEOF'
import sys, json
from pathlib import Path
d   = Path(sys.argv[1])
cfg = json.loads((d / "kage_config.json").read_text())
m   = cfg.get("models", {})
p   = cfg.get("providers", {})
t1  = m.get("tier1", {})
t2  = m.get("tier2", {})
tmpl = (d / "litellm_config.example.yaml").read_text()
tmpl = tmpl.replace("KAGE_TIER1_NAME",  t1.get("litellm_name", "tier-1-orchestrator"))
tmpl = tmpl.replace("KAGE_TIER1_MODEL", t1.get("ollama_model",  "qwen3:8b"))
tmpl = tmpl.replace("KAGE_TIER2_NAME",  t2.get("litellm_name", "tier-2-worker"))
tmpl = tmpl.replace("KAGE_TIER2_MODEL", t2.get("ollama_model",  "qwen3.6:35b"))
tmpl = tmpl.replace("KAGE_OLLAMA_URL",  p.get("ollama_url",     "http://localhost:11434"))
tmpl = tmpl.replace("KAGE_LITELLM_KEY", p.get("litellm_key",   "sk-orchestrator-local"))
(d / "litellm_config.yaml").write_text(tmpl)
PYEOF
    ok "litellm_config.yaml generated"
else
    ok "litellm_config.yaml exists"
fi

# Ollama model discovery
if command -v ollama &>/dev/null; then
    OLLAMA_MODELS=$("$DIR/.venv/bin/python3" -c "
import urllib.request, json, sys
try:
    data = json.loads(urllib.request.urlopen('http://localhost:11434/api/tags', timeout=2).read())
    names = [m['name'] for m in data.get('models', [])]
    print('\n'.join(names)) if names else print('')
except Exception:
    print('')
" 2>/dev/null || echo "")
    if [ -n "$OLLAMA_MODELS" ]; then
        ok "Ollama running — available models:"
        echo "$OLLAMA_MODELS" | while read -r model; do echo "      $model"; done
    else
        warn "Ollama not running or no models pulled yet"
        warn "  Pull tier-1: ollama pull qwen3:8b"
        warn "  Pull tier-2: ollama pull qwen3.6:35b"
        warn "  Pull embed:  ollama pull nomic-embed-text"
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
