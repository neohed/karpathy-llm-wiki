#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Config — override any of these via env vars
# ---------------------------------------------------------------------------
MODEL="${OLLAMA_MODEL:-qwen3:14b}"
HOST="${OLLAMA_HOST_ADDR:-0.0.0.0}"
PORT="${OLLAMA_PORT:-11434}"

# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

if ! command -v ollama &>/dev/null; then
    echo "Error: ollama not found in PATH"
    echo "  Install: curl -fsSL https://ollama.com/install.sh | sh"
    exit 1
fi

if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
    echo "Error: port ${PORT} is already in use"
    echo "  Kill the process or set OLLAMA_PORT=<other>"
    exit 1
fi

# ---------------------------------------------------------------------------
# Start server in background, pull model, then wait
# ---------------------------------------------------------------------------

export OLLAMA_HOST="${HOST}:${PORT}"

echo "Starting Ollama..."
ollama serve &
SERVER_PID=$!

# Kill server if this script exits for any reason
trap 'echo ""; echo "Stopping Ollama..."; kill $SERVER_PID 2>/dev/null; exit' EXIT INT TERM

# Wait for daemon to be ready (up to 30s)
echo "  Waiting for server to be ready..."
for i in $(seq 1 30); do
    if curl -s "http://localhost:${PORT}/" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

if ! curl -s "http://localhost:${PORT}/" >/dev/null 2>&1; then
    echo "Error: Ollama failed to start within 30 seconds"
    exit 1
fi

# Pull model if not already present
if ! ollama list 2>/dev/null | grep -q "^${MODEL}"; then
    echo "  Pulling ${MODEL} (first run — this will take a while)..."
    ollama pull "${MODEL}"
fi

echo ""
echo "  Model:  ${MODEL}"
echo "  API:    http://localhost:${PORT}/v1"
echo "  Ready."
echo ""

# Keep script running so Ctrl+C cleanly stops the server
wait $SERVER_PID
