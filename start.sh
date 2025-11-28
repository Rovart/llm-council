#!/usr/bin/env bash

# LLM Council - Start script (now attempts to start Ollama when enabled)

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "Starting LLM Council..."

# Load .env if present (export variables)
if [ -f ".env" ]; then
	echo "Loading .env"
	# shellcheck disable=SC1091
	export $(grep -v '^#' .env | xargs -I{} bash -c 'echo {}' ) || true
fi

# Helper to wait for HTTP service
wait_for_http() {
	local url="$1"
	local timeout=${2:-15}
	local waited=0
	while [ $waited -lt $timeout ]; do
		if command -v curl >/dev/null 2>&1; then
			if curl -s --fail "$url" >/dev/null 2>&1; then
				return 0
			fi
		else
			# fallback to nc if curl missing
			if command -v nc >/dev/null 2>&1; then
				nc -z localhost 11434 && return 0 || true
			fi
		fi
		sleep 1
		waited=$((waited + 1))
	done
	return 1
}

OLLAMA_STARTED_PID=""
if [ "${USE_OLLAMA:-false}" = "true" ] || [ "${USE_OLLAMA:-false}" = "1" ]; then
	echo "USE_OLLAMA is enabled — attempting to start Ollama (if installed)."
	if command -v "${OLLAMA_CLI_PATH:-ollama}" >/dev/null 2>&1; then
		OLLAMA_CMD="${OLLAMA_CLI_PATH:-ollama}"
		echo "Found Ollama CLI at: $OLLAMA_CMD"

		# Try common commands to start the Ollama daemon/server. This is best-effort.
		for cmd in daemon serve start; do
			echo "Trying: $OLLAMA_CMD $cmd ..."
			# Run in background with nohup so it doesn't get killed with this script
			nohup "$OLLAMA_CMD" "$cmd" > /tmp/ollama-$cmd.log 2>&1 &
			OLLAMA_STARTED_PID=$!
			# Wait briefly for HTTP service to appear
			if wait_for_http "${OLLAMA_API_URL:-http://localhost:11434}/api/models" 8; then
				echo "Ollama HTTP API available at ${OLLAMA_API_URL:-http://localhost:11434}"
				break
			else
				echo "$cmd didn't bring up HTTP API; killing pid $OLLAMA_STARTED_PID"
				kill "$OLLAMA_STARTED_PID" 2>/dev/null || true
				OLLAMA_STARTED_PID=""
			fi
		done

		if [ -z "$OLLAMA_STARTED_PID" ]; then
			echo "Could not auto-start Ollama HTTP API — you may need to start it manually."
			echo "Check /tmp/ollama-*.log for CLI output."
		fi
	else
		echo "Ollama CLI not found (path: ${OLLAMA_CLI_PATH:-ollama}). Skipping auto-start."
		echo "Install Ollama or set OLLAMA_USE_CLI=false and run a local server compatible with ${OLLAMA_API_URL:-http://localhost:11434}."
	fi
fi

# Start backend
echo "Starting backend on http://localhost:8001..."
uv run python -m backend.main &
BACKEND_PID=$!

# Wait a bit for backend to start
sleep 2

# Start frontend
echo "Starting frontend on http://localhost:5173..."
cd frontend
npm run dev &
FRONTEND_PID=$!

echo ""
echo "✓ LLM Council is running!"
echo "  Backend:  http://localhost:8001"
echo "  Frontend: http://localhost:5173"
if [ -n "$OLLAMA_STARTED_PID" ]; then
	echo "  Ollama:   ${OLLAMA_API_URL:-http://localhost:11434} (pid $OLLAMA_STARTED_PID)"
fi
echo ""
echo "Press Ctrl+C to stop servers"

# Cleanup on exit
cleanup() {
	echo "Stopping services..."
	kill "$FRONTEND_PID" "$BACKEND_PID" 2>/dev/null || true
	if [ -n "$OLLAMA_STARTED_PID" ]; then
		echo "Stopping Ollama (pid $OLLAMA_STARTED_PID)"
		kill "$OLLAMA_STARTED_PID" 2>/dev/null || true
	fi
	exit 0
}

trap cleanup SIGINT SIGTERM
wait
