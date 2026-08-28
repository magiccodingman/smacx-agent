#!/bin/sh
set -eu

project_dir=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
mcp_log="$project_dir/runtime/hermes-test-mcp.log"
prompt_file=${SMACX_HERMES_PROMPT_FILE:-$project_dir/scripts/hermes_test_prompt.txt}
python_bin=${SMACX_PYTHON:-$HOME/.hermes/hermes-agent/venv/bin/python}

if [ ! -f "$prompt_file" ]; then
    echo "Hermes prompt file does not exist: $prompt_file" >&2
    exit 1
fi

if ss -ltn | rg -q '127\.0\.0\.1:47814'; then
    echo "Port 47814 is already in use. Stop smacx-agent-mcp.service before this contained test." >&2
    exit 1
fi

cleanup() {
    if [ "${mcp_pid:-}" ]; then
        kill "$mcp_pid" >/dev/null 2>&1 || true
        wait "$mcp_pid" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT HUP INT TERM

PYTHONPATH="$project_dir/src" \
    "$python_bin" "$project_dir/src/smacx_mcp.py" \
    >"$mcp_log" 2>&1 &
mcp_pid=$!

ready=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if ! kill -0 "$mcp_pid" >/dev/null 2>&1; then
        echo "Contained MCP exited during startup; see $mcp_log" >&2
        exit 1
    fi
    if ss -ltn | rg -q '127\.0\.0\.1:47814'; then
        ready=1
        break
    fi
    sleep 0.25
done
if [ "$ready" -ne 1 ]; then
    echo "Contained MCP did not start; see $mcp_log" >&2
    exit 1
fi

cd "$project_dir"
prompt=$(cat "$prompt_file")
hermes --in "$project_dir" -m Qwen3.8-27B --reasoning low \
    --usage-file "$project_dir/runtime/hermes-test-usage.json" \
    -z "$prompt"
