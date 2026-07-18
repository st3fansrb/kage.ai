#!/usr/bin/env bash
# Opreste exclusiv daemonul Freqtrade dry-run pornit de start_freqtrade_dryrun.sh.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIDFILE="$ROOT/.logs/freqtrade-dryrun.pid"

if [[ ! -f "$PIDFILE" ]]; then
  echo "Freqtrade dry-run nu pare pornit (pidfile lipseste)."
  exit 0
fi
PID="$(cat "$PIDFILE")"
if kill -0 "$PID" 2>/dev/null; then
  kill -TERM "$PID"
  echo "Semnal de oprire trimis daemonului $PID."
else
  echo "Procesul $PID nu mai exista."
fi
rm -f "$PIDFILE"
