#!/usr/bin/env bash
# Don't use set -e — we want all tests to run even if one fails
set -uo pipefail

BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
FORCE_MODEL="${OLLAMA_MODEL:-}"  # override auto-detected model if set

PASS=0
FAIL=0

GREEN="\033[0;32m"
RED="\033[0;31m"
RESET="\033[0m"

pass() { echo -e "  ${GREEN}PASS${RESET}  $1"; PASS=$((PASS + 1)); }
fail() { echo -e "  ${RED}FAIL${RESET}  $1"; FAIL=$((FAIL + 1)); }

echo "Ollama server: ${BASE_URL}"
echo ""

# ---------------------------------------------------------------------------
# 1. Health
# ---------------------------------------------------------------------------
echo "1. Health check"

http_code=$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
    "${BASE_URL}/" 2>/dev/null || echo "000")

if [[ "$http_code" == "200" ]]; then
    pass "GET / → 200"
else
    fail "GET / → ${http_code} (is Ollama running? Try: ollama serve)"
fi

# ---------------------------------------------------------------------------
# 2. Models
# ---------------------------------------------------------------------------
echo ""
echo "2. Models endpoint"

models_json=$(curl -s --max-time 5 "${BASE_URL}/v1/models" 2>/dev/null || echo "")
model=$(echo "$models_json" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d['data'][0]['id'])" \
    2>/dev/null || echo "")

# Allow env var override so we can test a specific model
[[ -n "$FORCE_MODEL" ]] && model="$FORCE_MODEL"

if [[ -n "$model" ]]; then
    pass "GET /v1/models → ${model}"
else
    fail "GET /v1/models → could not parse model id"
fi

# ---------------------------------------------------------------------------
# 3. Chat completion (short deterministic prompt)
# ---------------------------------------------------------------------------
echo ""
echo "3. Chat completion"

if [[ -z "$model" ]]; then
    fail "Skipped — no model available from step 2"
else
    payload=$(python3 -c "
import json
print(json.dumps({
    'model': '${model}',
    'messages': [{'role': 'user', 'content': 'Reply with only the number: what is 2 + 2?'}],
    'max_tokens': 64,
    'temperature': 0,
    'think': False
}))
")

    start_ms=$(date +%s%3N)
    completion_json=$(curl -s --max-time 30 -X POST "${BASE_URL}/v1/chat/completions" \
        -H "Content-Type: application/json" \
        -d "$payload" 2>/dev/null || echo "")
    elapsed=$(( $(date +%s%3N) - start_ms ))

    content=$(echo "$completion_json" | python3 -c \
        "import sys,json; d=json.load(sys.stdin); print(d['choices'][0]['message']['content'].strip())" \
        2>/dev/null || echo "")

    if [[ -n "$content" ]]; then
        pass "POST /v1/chat/completions → \"${content}\"  (${elapsed}ms)"
    else
        fail "POST /v1/chat/completions → empty or unparseable response"
        # Print the raw response to help diagnose
        if [[ -n "$completion_json" ]]; then
            echo "       Raw: ${completion_json:0:300}"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "─────────────────────────────────"
echo -e "  ${PASS} passed  ${FAIL} failed"
echo "─────────────────────────────────"

[[ $FAIL -eq 0 ]]
