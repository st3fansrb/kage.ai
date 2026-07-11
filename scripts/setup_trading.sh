#!/usr/bin/env bash
# WP-T slice 2: creează .trading-venv cu freqtrade instalat.
#
# Freqtrade are ~200 dependențe care conflictuează cu Kage (aiohttp, sqlalchemy,
# numpy versiuni diferite). Acest script le izolează într-un venv separat.
#
# Utilizare:
#   bash scripts/setup_trading.sh            # instalare completă
#   bash scripts/setup_trading.sh --check    # doar verificare

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="$PROJECT_ROOT/.trading-venv"
USERDATA="$PROJECT_ROOT/trading/ft_userdata"

# ── helpers ──────────────────────────────────────────────────────────────────
info()  { printf "\033[1;34m[trading-setup]\033[0m %s\n" "$1"; }
ok()    { printf "\033[1;32m[trading-setup]\033[0m %s\n" "$1"; }
err()   { printf "\033[1;31m[trading-setup]\033[0m %s\n" "$1" >&2; }

check_only() {
    local status=0
    if [ -d "$VENV_DIR" ] && "$VENV_DIR/bin/python" -c "import freqtrade" 2>/dev/null; then
        ok "✅ .trading-venv OK — $(\"$VENV_DIR/bin/freqtrade\" --version 2>/dev/null || echo 'versiune necunoscută')"
    else
        err "❌ .trading-venv lipsește sau freqtrade nu e instalat"
        status=1
    fi
    if [ -d "$USERDATA/strategies" ]; then
        ok "✅ ft_userdata/strategies/ există"
    else
        err "❌ ft_userdata/strategies/ lipsește"
        status=1
    fi
    return $status
}

# ── --check mode ─────────────────────────────────────────────────────────────
if [ "${1:-}" = "--check" ]; then
    check_only
    exit $?
fi

# ── 1. Creează venv-ul ───────────────────────────────────────────────────────
if [ -d "$VENV_DIR" ]; then
    info "Venv-ul .trading-venv există deja, skip creare"
else
    info "Creare .trading-venv (Python 3.12)..."
    python3.12 -m venv "$VENV_DIR" 2>/dev/null \
        || python3 -m venv "$VENV_DIR"
    ok "Venv creat: $VENV_DIR"
fi

# ── 2. Instalează freqtrade ──────────────────────────────────────────────────
info "Instalare/actualizare freqtrade + scipy..."
"$VENV_DIR/bin/pip" install --upgrade pip -q
"$VENV_DIR/bin/pip" install freqtrade scipy -q

FT_VERSION=$("$VENV_DIR/bin/freqtrade" --version 2>/dev/null || echo "necunoscut")
ok "Freqtrade instalat: $FT_VERSION"

# ── 3. Creează userdata (dacă nu există) ─────────────────────────────────────
if [ -d "$USERDATA/strategies" ]; then
    info "ft_userdata/ există deja, skip"
else
    info "Creare structură ft_userdata/..."
    mkdir -p "$USERDATA/strategies"
    mkdir -p "$USERDATA/data"
    mkdir -p "$USERDATA/backtest_results"
    ok "Structură creată: $USERDATA"
fi

# ── 4. Verificare finală ────────────────────────────────────────────────────
echo ""
info "Verificare finală..."
check_only
echo ""
ok "Setup complet! Pentru a rula un backtest:"
echo "  source .trading-venv/bin/activate"
echo "  freqtrade backtesting --strategy SampleStrategy --config trading/ft_config_dry.json"
