#!/usr/bin/env bash
# Wrapper for LaunchAgent to keep Kage running 24/7
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "=== Kage LaunchAgent Wrapper started at $(date) ==="

# Initial start
./start_all.sh --no-browser

# Keep-alive loop
while true; do
  # Check if main components are up
  ORCH_UP=0
  LITE_UP=0
  
  nc -z 127.0.0.1 4001 && ORCH_UP=1
  nc -z 127.0.0.1 4000 && LITE_UP=1
  
  if [ $ORCH_UP -eq 0 ] || [ $LITE_UP -eq 0 ]; then
    echo "$(date): Component down (Orch:$ORCH_UP, Lite:$LITE_UP). Restarting..."
    ./start_all.sh --no-browser
  fi
  
  sleep 60
done
