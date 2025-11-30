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
		# Try a few candidate endpoints for Ollama model list
		candidates=("$url" "${url%/}/" "${url%/}/models" "${url%/}/v1/models" "${url%/}/api/models")
		if command -v curl >/dev/null 2>&1; then
			for u in "${candidates[@]}"; do
				if curl -s --fail "$u" >/dev/null 2>&1; then
					return 0
				fi
			done
		else
			# fallback to nc: make sure something is listening on the host port
			if command -v nc >/dev/null 2>&1; then
				# Parse port from URL
				port=$(echo "$url" | awk -F: '{print $NF}')
				nc -z localhost "$port" && return 0 || true
			fi
		fi
		sleep 1
		waited=$((waited + 1))
	done
	return 1
}

PID_DIR="$ROOT_DIR/.pids"
mkdir -p "$PID_DIR"
OLLAMA_STARTED_PID=""
if [ "${USE_OLLAMA:-false}" = "true" ] || [ "${USE_OLLAMA:-false}" = "1" ]; then
	echo "USE_OLLAMA is enabled — attempting to start Ollama (if installed)."
	if command -v "${OLLAMA_CLI_PATH:-ollama}" >/dev/null 2>&1; then
		OLLAMA_CMD="${OLLAMA_CLI_PATH:-ollama}"
		echo "Found Ollama CLI at: $OLLAMA_CMD"

		# If user provided an explicit start command, use it verbatim (useful when GUI
		# runner or custom start script is required). Example: export OLLAMA_START_COMMAND="ollama serve --port 11434"
		if [ -n "${OLLAMA_START_COMMAND:-}" ]; then
			echo "Using explicit OLLAMA_START_COMMAND: ${OLLAMA_START_COMMAND}"
			# shellcheck disable=SC2086
			nohup ${OLLAMA_START_COMMAND} > /tmp/ollama-start.log 2>&1 &
			OLLAMA_STARTED_PID=$!
			if wait_for_http "${OLLAMA_API_URL:-http://localhost:11434}" 12; then
				echo "Ollama HTTP API available at ${OLLAMA_API_URL:-http://localhost:11434}"
			else
				echo "OLLAMA_START_COMMAND did not bring up HTTP API in time; checking /tmp/ollama-start.log for port hints"
				# Try to parse the log for a listening port and update .env accordingly
				if [ -f /tmp/ollama-start.log ]; then
					# common log patterns that include http://127.0.0.1:11434 or :11434
					PORT_LINE=$(grep -Eo "https?://[0-9.]+:[0-9]+" /tmp/ollama-start.log | head -n1 || true)
					if [ -n "$PORT_LINE" ]; then
						# extract base url
						BASE_URL=$PORT_LINE
						# write/update .env with OLLAMA_API_URL
						if grep -q "^OLLAMA_API_URL=" .env 2>/dev/null; then
							sed -i.bak "s|^OLLAMA_API_URL=.*|OLLAMA_API_URL=$BASE_URL|" .env || true
						else
							echo "OLLAMA_API_URL=$BASE_URL" >> .env
						fi
						echo "Updated .env with OLLAMA_API_URL=$BASE_URL"
						# export for current run
						export OLLAMA_API_URL="$BASE_URL"
						# Try again briefly
						if wait_for_http "${OLLAMA_API_URL}/api/models" 6; then
							echo "Ollama HTTP API available at ${OLLAMA_API_URL}"
						fi
					fi
				fi
				if [ -z "${OLLAMA_API_URL:-}" ]; then
					echo "Cannot detect Ollama API URL automatically. Please start Ollama and set OLLAMA_API_URL in .env or set OLLAMA_USE_CLI=true to use the CLI fallback."
				fi
			fi

		else
			# Try common commands to start the Ollama daemon/server. This is best-effort.
			for cmd in daemon serve start; do
				echo "Trying: $OLLAMA_CMD $cmd ..."
				# Run in background with nohup so it doesn't get killed with this script
				nohup "$OLLAMA_CMD" "$cmd" > /tmp/ollama-$cmd.log 2>&1 &
				OLLAMA_STARTED_PID=$!

				# Wait briefly for HTTP service to appear
				if wait_for_http "${OLLAMA_API_URL:-http://localhost:11434}" 8; then
					echo "Ollama HTTP API available at ${OLLAMA_API_URL:-http://localhost:11434}"
					break
				else
					# If it didn't, inspect the log for common errors
					echo "$cmd did not bring up HTTP API (check /tmp/ollama-$cmd.log)"
					if grep -q "bind: address already in use" /tmp/ollama-$cmd.log 2>/dev/null; then
						echo "Port 11434 appears to be in use. Attempting to detect the process occupying the port."
						if command -v lsof >/dev/null 2>&1; then
							PID_INFO=$(lsof -nP -iTCP:11434 -sTCP:LISTEN 2>/dev/null | awk 'NR==2{print $2" " $1}') || true
							if [ -n "$PID_INFO" ]; then
								echo "Found listener on 11434: $PID_INFO"
								# If the detected process looks like Ollama, mark API as available
								if echo "$PID_INFO" | grep -qi "ollama"; then
									echo "Detected Ollama process on port 11434; using existing API"
									export OLLAMA_API_URL="http://localhost:11434"
									OLLAMA_STARTED_PID=""
									break
								fi
							else
								echo "No process found via lsof for 11434; maybe it was a transient conflict. Check /tmp/ollama-$cmd.log"
							fi
						else
							echo "lsof not available on this system; can't detect process using the port."
						fi
					fi

					# If we couldn't detect an existing API, leave the process running for manual inspection and move on.
					# Try to detect a listening port from logs and set .env accordingly, if present
					LOGFILE="/tmp/ollama-$cmd.log"
					if [ -f "$LOGFILE" ]; then
						LISTEN_LINE=$(grep -Eo "Listening on [0-9.]+:[0-9]+" "$LOGFILE" | head -n1 || true)
						if [ -n "$LISTEN_LINE" ]; then
							PORT_PART=$(echo "$LISTEN_LINE" | awk '{print $3}')
							echo "Detected listening line in $LOGFILE: $LISTEN_LINE"
							if [ -n "$PORT_PART" ]; then
								BASE_URL="http://$PORT_PART"
								echo "Updating .env OLLAMA_API_URL to $BASE_URL"
								if grep -q "^OLLAMA_API_URL=" .env 2>/dev/null; then
									sed -i.bak "s|^OLLAMA_API_URL=.*|OLLAMA_API_URL=$BASE_URL|" .env || true
								else
									echo "OLLAMA_API_URL=$BASE_URL" >> .env
								fi
								export OLLAMA_API_URL="$BASE_URL"
								# quickly check whether the API is reachable by trying root or known endpoints
								if wait_for_http "${OLLAMA_API_URL}" 6; then
									echo "Found Ollama API at ${OLLAMA_API_URL}"
									OLLAMA_STARTED_PID=""
									break
								fi
							fi
						fi
					fi
					echo "Leaving process $OLLAMA_STARTED_PID running for diagnostics (not killing it)."
				fi
			done
			if [ -z "$OLLAMA_STARTED_PID" ]; then
				echo "Could not auto-start Ollama HTTP API — you may need to start it manually."
				echo "Check /tmp/ollama-*.log for CLI output."
			fi
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
echo "$BACKEND_PID" > "$PID_DIR/backend.pid"

# Wait a bit for backend to start
sleep 2

# Start frontend
echo "Starting frontend on http://localhost:5173..."
cd frontend
npm run dev &
FRONTEND_PID=$!
echo "$FRONTEND_PID" > "$PID_DIR/frontend.pid"

echo ""
echo "✓ LLM Council is running!"
echo "  Backend:  http://localhost:8001"
echo "  Frontend: http://localhost:5173"
if [ -n "$OLLAMA_STARTED_PID" ]; then
	echo "  Ollama:   ${OLLAMA_API_URL:-http://localhost:11434} (pid $OLLAMA_STARTED_PID)"
	echo "$OLLAMA_STARTED_PID" > "$PID_DIR/ollama.pid"
fi
echo ""
echo "Press Ctrl+C to stop servers"

# Cleanup on exit
cleanup() {
	echo "Stopping services via stop.sh"
	"$ROOT_DIR/stop.sh" || true
	exit 0
}

trap cleanup SIGINT SIGTERM
wait
