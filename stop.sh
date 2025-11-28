#!/usr/bin/env bash
set -euo pipefail

# Stop script for LLM Council: kills backend, frontend, and Ollama processes started by start.sh
ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_DIR="$ROOT_DIR/.pids"
mkdir -p "$PID_DIR"

kill_pidfile() {
  local pidfile="$1"
  if [ -f "$pidfile" ]; then
    pid=$(cat "$pidfile") || true
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      echo "Stopping PID $pid from $pidfile"
      kill "$pid" 2>/dev/null || true
      sleep 1
      if kill -0 "$pid" 2>/dev/null; then
        echo "PID $pid didn't exit, killing."; kill -9 "$pid" 2>/dev/null || true
      fi
    fi
    rm -f "$pidfile"
  fi
}

echo "Stopping LLM Council services..."

# Kill by pid files if present
kill_pidfile "$PID_DIR/backend.pid"
kill_pidfile "$PID_DIR/frontend.pid"
kill_pidfile "$PID_DIR/ollama.pid"

# Fallback: try to kill by process name (only processes owned by $USER)
MYUSER=$(whoami)
echo "Killing processes by name (uvicorn, node, vite, ollama) for user $MYUSER"
pkill -u "$MYUSER" -f 'uvicorn' 2>/dev/null || true
pkill -u "$MYUSER" -f 'backend.main' 2>/dev/null || true
pkill -u "$MYUSER" -f 'npm run dev' 2>/dev/null || true
pkill -u "$MYUSER" -f 'vite' 2>/dev/null || true
pkill -u "$MYUSER" -f 'ollama' 2>/dev/null || true

echo "Done."
