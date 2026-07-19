#!/usr/bin/env bash
# AI Orchestration System v2 — oprește toate serviciile.
set -uo pipefail

DIR="$(cd "$(dirname "$0")/.." && pwd)"

kill_port() {
  local name="$1" port="$2"
  local pids
  pids=$(lsof -ti :"$port" 2>/dev/null || true)
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill 2>/dev/null || true
    echo "  ✓ $name (:$port) oprit"
  else
    echo "  — $name (:$port) nu rula"
  fi
}

echo "=== Opresc AI Orchestration System v2 ==="

kill_port "Orchestrator" 4001
kill_port "LiteLLM"      4000
# WP-AF: Airflow standalone = mai multe procese (webserver :8080 + scheduler + triggerer).
kill_port "Airflow UI"   8080
if pgrep -f "$DIR/.airflow-venv/bin/airflow" >/dev/null 2>&1; then
  pkill -f "$DIR/.airflow-venv/bin/airflow" 2>/dev/null || true
  echo "  ✓ Airflow (scheduler/triggerer) oprit"
fi
# Ollama nu e oprit intenționat — rulează și pentru alte use-case-uri
# Widget nu e oprit — rămâne în menubar și arată 🔴 (sistem offline)

echo "=== Sistem oprit. Widget-ul rămâne activ (🔴). ==="
