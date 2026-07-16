#!/usr/bin/env bash
# Porneste daemonul Freqtrade Kage. Exclusiv paper/dry-run; fara autostart.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.trading-venv"
USERDATA="$ROOT/trading/ft_userdata"
EXAMPLE="$ROOT/trading/SampleStrategy_example.py"
STRATEGY="$USERDATA/strategies/SampleStrategy.py"
PIDFILE="$ROOT/.logs/freqtrade-dryrun.pid"
LOGFILE="$ROOT/.logs/freqtrade-dryrun.log"

if [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
  echo "Freqtrade dry-run ruleaza deja (pid $(cat "$PIDFILE"))."
  exit 1
fi
"$VENV/bin/python" -c 'import freqtrade' 2>/dev/null || {
  echo "Freqtrade lipseste. Ruleaza mai intai: bash scripts/setup_trading.sh" >&2; exit 1;
}
mkdir -p "$USERDATA/strategies" "$ROOT/.logs"
if [[ ! -f "$STRATEGY" ]]; then
  cp "$EXAMPLE" "$STRATEGY"
  echo "Strategia exemplu a fost copiata local in ft_userdata/."
fi

cd "$ROOT"
nohup "$VENV/bin/python" -m trading.freqtrade_daemon --log "$LOGFILE" \
  > /dev/null 2>&1 &
PID=$!
echo "$PID" > "$PIDFILE"
sleep 1
if kill -0 "$PID" 2>/dev/null; then
  echo "Freqtrade dry-run pornit (pid $PID). Log: $LOGFILE"
else
  rm -f "$PIDFILE"
  echo "Daemonul s-a oprit; verifica $LOGFILE" >&2
  exit 1
fi
